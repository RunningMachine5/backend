"""고객 대응 챗봇의 한 턴을 LangGraph 그래프로 실행한다.

설계는 docs/customer-chatbot/README.md 의 2.3~2.6 이다.

**구동 방식: 턴 단위 invoke.** 고객 입력 하나가 그래프 한 번의 실행이고, 그 턴에
고객에게 보낼 메시지를 만든 뒤 그래프는 END 로 끝난다. 다음 질문을 기다리는 동안
그래프는 살아 있지 않으며, 진행 상태(``question_step``·재시도 횟수)만
``InMemorySaver`` 체크포인터에 ``thread_id = chat_session_id`` 로 남아 다음 invoke 가
이어받는다.

``interrupt()`` + ``Command(resume=...)`` 로 그래프를 대화 중간에 멈춰 세우지 않은 이유:

- 챗봇의 입력 경로가 HTTP 요청 하나뿐이라 멈춤 지점이 곧 요청 경계다. 요청마다
  그래프가 끝나는 편이 API(7단계)와 1:1로 대응하고, 재개 지점을 따로 관리할 필요가 없다.
- 체크포인터가 ``InMemorySaver`` 라 서버 재시작 시 진행 상태가 사라진다(스키마 3.4).
  턴 단위 invoke 는 ``attempt_no`` 만 유실되고 ``question_step`` 은
  DB(``chat_sessions.question_step``)에서 다시 seed 할 수 있지만, 재개 방식은 멈춘
  노드 자체가 사라져 그 턴을 이어갈 수 없다.
- 분기(판정 5종·버튼 3종)를 노드와 조건부 엣지로 그대로 표현할 수 있어, 재개 흐름을
  모킹하지 않고 분기만 검증하는 테스트가 가능하다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from sqlmodel import Session

from app.data.model.chatbot import (
    ChatAnswer,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.data.model.transaction import Transaction
from app.dto.chatbot import AnswerQualityVerdict, ChatButtonAction
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.answer_evaluator import AnswerEvaluator
from app.services.chatbot.chat_scoring import score_chat_fraud_circumstances
from app.services.chatbot.extractors import (
    ChatbotExtractionError,
    FraudCircumstanceExtractor,
    GuideSearchQueryExtractor,
)
from app.services.chatbot.guide_responder import GuideResponder
from app.services.chatbot.messages import (
    END_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    NEXT_QUESTION_MESSAGE,
    NON_ANSWER_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_HANDOFF_MESSAGE,
    render_initial_notification,
)
from app.services.chatbot.questions import render_question


logger = logging.getLogger(__name__)


# 한 질문에서 허용하는 최대 응답 수. 최초 응답 1회 + 재질문 2회 (PRD 2.4 조건 1).
MAX_ATTEMPTS_PER_QUESTION = 3

# 프로세스 전역 체크포인터. 턴과 턴 사이의 진행 상태는 여기에만 있다.
_CHECKPOINTER = InMemorySaver()


class ChatTurnRejectedError(RuntimeError):
    """현재 세션 상태에서 받을 수 없는 입력이 들어온 경우."""


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    """한 턴에서 고객에게 보낸 메시지와 턴이 끝난 뒤의 세션 상태."""

    messages: tuple[str, ...]
    status: ChatSessionStatus
    question_step: int


class ChatGraphState(TypedDict, total=False):
    """그래프 상태.

    answer 와 chat_session 은 id로 저장한다
    """

    # 이번 턴의 입력
    event: Literal[
        "INITIAL_NOTIFICATION",
        "BUTTON_ACTION",
        "CUSTOMER_MESSAGE",
    ]
    button_action: str | None  # event가 BUTTON_ACTION일 때만 사용
    message_text: str | None
    question_step: int  # 턴 사이에 유지되는 진행 상태
    attempt_no: int # 현재 시도 횟수 
    # 턴 안에서만 쓰는 값
    route: str # 다음 경로의 위치
    answer_id: int | None
    outbound: list[str]


class CustomerChatbotPipeline:
    """세션 하나의 상담 흐름을 조정하고 턴 단위로 커밋한다."""

    def __init__(
        self,
        *,
        session: Session,
        chat_session: ChatSession,
        evaluator: AnswerEvaluator | None = None,
        guide_search_query_extractor: GuideSearchQueryExtractor | None = None,
        fraud_circumstance_extractor: FraudCircumstanceExtractor | None = None,
        guide_responder: GuideResponder | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.session = session
        self.chat_session = chat_session
        self.repository = ChatSessionRepository(session)
        # LLM 클라이언트는 첫 호출까지 만들지 않는다. 최초 알림과 버튼 처리만 하는
        # 턴에서 OPENAI_API_KEY 가 없다는 이유로 실패하지 않게 한다.
        self._evaluator = evaluator
        self._guide_search_query_extractor = guide_search_query_extractor
        self._fraud_circumstance_extractor = fraud_circumstance_extractor
        self._guide_responder = guide_responder
        self.graph = self._build_graph(checkpointer or _CHECKPOINTER)

    # ------------------------------------------------------------------
    # 공개 진입점 — API(7단계)가 부르는 턴 단위 실행
    # ------------------------------------------------------------------

    def send_initial_notification(self) -> ChatTurnResult:
        """B.1 최초 알림을 출력한다. 상태는 바뀌지 않는다(PRD 2.3)."""

        return self._run_turn({"event": "INITIAL_NOTIFICATION"})

    def handle_button(self, action: ChatButtonAction) -> ChatTurnResult:
        """최초 알림 뒤 버튼 3종을 처리한다."""

        if self.chat_session.status != ChatSessionStatus.URL_SENT.value:
            raise ChatTurnRejectedError(
                "버튼 선택은 URL_SENT 상태에서만 처리할 수 있습니다"
            )
        return self._run_turn(
            {"event": "BUTTON_ACTION", "button_action": action.value}
        )

    def handle_message(self, message_text: str) -> ChatTurnResult:
        """고객 답변 한 건을 평가하고 다음 흐름까지 진행한다."""

        if self.chat_session.status != ChatSessionStatus.IN_PROGRESS.value:
            raise ChatTurnRejectedError(
                "고객 답변은 IN_PROGRESS 상태에서만 처리할 수 있습니다"
            )
        if self.chat_session.question_step < 1:
            raise ChatTurnRejectedError("답변을 기다리는 질문이 없습니다")
        return self._run_turn(
            {"event": "CUSTOMER_MESSAGE", "message_text": message_text}
        )

    # ------------------------------------------------------------------
    # 턴 실행과 트랜잭션 소유
    # ------------------------------------------------------------------

    def _run_turn(self, turn_input: dict[str, Any]) -> ChatTurnResult:
        """챗봇 그래프 한 주기"""

        config = {
            "configurable": {"thread_id": self.chat_session.chat_session_id}
        }
        # outbound 는 턴마다 비운다. 리듀서를 두지 않아 입력이 이전 턴 값을 덮는다.
        # 이번 턴의 입력 상태
        state_input: dict[str, Any] = {
            "button_action": None,# 이전 턴의 버튼 선택 제거
            "message_text": None,# 이전 고객 답변 제거
            "answer_id": None,# 이전 ChatAnswer ID 제거 (고객응답과 평가 결과가 저장된 객체 id)
            "outbound": [],# 이전 턴에 보낸 메시지 제거
            **turn_input,
        }
        state_input.update(self._seed_progress_state(config))

        try:
            final_state = self.graph.invoke(state_input, config=config)
            self.repository.update_question_step(
                self.chat_session,
                final_state.get("question_step", 0),
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        self.session.refresh(self.chat_session)
        return ChatTurnResult(
            messages=tuple(final_state.get("outbound", [])),
            status=ChatSessionStatus(self.chat_session.status),
            question_step=self.chat_session.question_step,
        )

    def _seed_progress_state(self, config: dict[str, Any]) -> dict[str, Any]:
        """체크포인트가 없는 첫 턴(또는 서버 재시작 후)의 진행 상태를 만든다.

        ``question_step`` 은 DB 값에서 복구할 수 있지만 재시도 횟수는 메모리에만
        있으므로 유실을 감수하고 초기화한다(스키마 3.4).
        """

        if self.graph.get_state(config).values:
            return {}
        return {
            "question_step": self.chat_session.question_step,
            "attempt_no": 0,
        }

    # ------------------------------------------------------------------
    # 그래프 조립
    # ------------------------------------------------------------------

    def _build_graph(self, checkpointer: Any) -> Any:
        builder = StateGraph(ChatGraphState)
        builder.add_node("notify", self._notify)
        builder.add_node("button", self._button)
        builder.add_node("ask_question", self._ask_question)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("reask", self._reask)
        builder.add_node(
            "process_sufficient_answer",
            self._process_sufficient_answer,
        )
        builder.add_node("announce_next", self._announce_next)
        builder.add_node("finish", self._finish)

        builder.set_conditional_entry_point(
            lambda state: state["event"],
            {
                "INITIAL_NOTIFICATION": "notify",
                "BUTTON_ACTION": "button",
                "CUSTOMER_MESSAGE": "evaluate",
            },
        )
        builder.add_edge("notify", END)
        builder.add_conditional_edges(
            "button",
            _follow_route,
            {"ask_question": "ask_question", "end": END},
        )
        builder.add_edge("ask_question", END)
        builder.add_conditional_edges(
            "evaluate",
            _follow_route,
            {
                "process_sufficient_answer": "process_sufficient_answer",
                "reask": "reask",
                "announce_next": "announce_next",
                "finish": "finish",
            },
        )
        builder.add_edge("reask", END)
        # SUFFICIENT 와 전이 안내는 모두 다음 질문으로 이어진다(PRD 2.5 응답 후 흐름).
        builder.add_edge("process_sufficient_answer", "ask_question")
        builder.add_edge("announce_next", "ask_question")
        builder.add_edge("finish", END)
        return builder.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------
    # 노드
    # ------------------------------------------------------------------

    def _notify(self, state: ChatGraphState) -> dict[str, Any]:
        """B.1 최초 알림. 버튼 3종은 이 메시지 뒤에 프론트가 표시한다."""

        transaction = self._load_transaction()
        return {
            "outbound": self._emit(
                state.get("outbound", []),
                render_initial_notification(
                    transaction_datetime=transaction.transaction_datetime,
                    transaction_amount=transaction.transaction_amount,
                ),
            )
        }

    def _button(self, state: ChatGraphState) -> dict[str, Any]:
        """챗봇시작/상담사연결/상담종료 버튼 눌렀을때 분기"""

        action = state.get("button_action")
        if action == ChatButtonAction.START_CHAT.value:
            self.repository.update_status(
                self.chat_session,
                ChatSessionStatus.IN_PROGRESS,
            )
            # "챗봇 상담"은 출력 문구 없이 바로 첫 질문으로 간다(B.2).
            return {"route": "ask_question"}

        if action == ChatButtonAction.REQUEST_HANDOFF.value:
            # 상담을 시작하지 않았으므로 completed_at 은 남기지 않는다.
            self.repository.request_handoff(self.chat_session)
            return {
                "route": "end",
                "outbound": self._emit(state.get("outbound", []), HANDOFF_WAITING_MESSAGE),
            }

        if action == ChatButtonAction.END_CHAT.value:
            self.repository.set_session_complete(
                self.chat_session,
                completed_at=datetime.now(UTC),
            )
            return {
                "route": "end",
                "outbound": self._emit(state.get("outbound", []), END_CHAT_MESSAGE),
            }

        raise ChatTurnRejectedError(f"알 수 없는 버튼 액션입니다: {action}")

    def _ask_question(self, state: ChatGraphState) -> dict[str, Any]:
        """다음 질문을 출력하고 재시도 횟수를 초기화한다."""

        question_step = state.get("question_step", 0) + 1
        # 몇번째 질문인지, 어떤 사기유형인지에 따라 질문이 달라진다
        question_text = render_question(
            question_step=question_step,
            top_fraud_types=self.chat_session.top_fraud_types,
        )
        return {
            "question_step": question_step,
            "attempt_no": 0,
            "outbound": self._emit(state.get("outbound", []), question_text),
        }

    def _evaluate(self, state: ChatGraphState) -> dict[str, Any]:
        """고객 답변을 저장·평가하고 판정별 경로를 정한다(PRD 2.4 조건 2)."""

        message_text = state.get("message_text") or ""
        question_step = state["question_step"]
        attempt_no = min(
            state.get("attempt_no", 0) + 1,
            MAX_ATTEMPTS_PER_QUESTION,
        )

        # 고객 답변 저장
        message = self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text=message_text,
        )
        # 고객 답변 평가
        outcome = self.evaluator.evaluate(
            question_text=render_question(
                question_step=question_step,
                top_fraud_types=self.chat_session.top_fraud_types,
            ),
            customer_answer=message_text,
        )
        # 고객 답변 평가 결과 확인(충분/모호/답변없음/거부/종료희망)
        verdict = outcome.routing_verdict
        # TOO_VAGUE 또는 NON_ANSWER가 세 번째 응답까지 이어지면 재질문을 중단하고,
        # 마지막 응답을 해당 질문의 채택 답변으로 기록한 뒤 다음 질문으로 넘어간다
        retry_exhausted = (
            verdict
            in (
                AnswerQualityVerdict.TOO_VAGUE,
                AnswerQualityVerdict.NON_ANSWER,
            )
            and attempt_no >= MAX_ATTEMPTS_PER_QUESTION
        )
        is_adopted = verdict is AnswerQualityVerdict.SUFFICIENT or retry_exhausted

        # 고객 메시지에 대한 평가 데이터를 맵핑한다
        answer = self.repository.add_answer(
            self.chat_session,
            message=message,
            question_step=question_step,
            attempt_no=attempt_no,
            quality_verdict=outcome.quality_verdict,
            verdict_skip_reason=outcome.verdict_skip_reason,
            is_adopted=is_adopted,
        )
        self.session.flush()

        # 이 응답을 보고 _follow_route 가 실행된다
        return {
            "attempt_no": attempt_no,
            "answer_id": answer.answer_id,
            "route": _route_for_verdict(verdict, retry_exhausted=retry_exhausted),
        }

    def _reask(self, state: ChatGraphState) -> dict[str, Any]:
        """재질문 안내만 출력한다. question_step 은 그대로다(PRD 2.4)."""

        verdict = self._last_verdict(state)
        guidance = (
            TOO_VAGUE_MESSAGE
            if verdict == AnswerQualityVerdict.TOO_VAGUE.value
            else NON_ANSWER_MESSAGE
        )
        return {"outbound": self._emit(state.get("outbound", []), guidance)}

    def _announce_next(self, state: ChatGraphState) -> dict[str, Any]:
        """다음 질문으로 넘어간다는 전이 안내(B.3 REFUSAL 문구).

        REFUSAL, 평가 LLM 실패(EVALUATOR_FAILED), 재시도 초과가 같은 문구를 쓴다.
        """

        return {"outbound": self._emit(state.get("outbound", []), NEXT_QUESTION_MESSAGE)}

    def _process_sufficient_answer(
        self,
        state: ChatGraphState,
    ) -> dict[str, Any]:
        """채택 답변에서 가이드 검색 질의와 사기 정황을 각각 독립 실행한다.

        한 경로가 실패해도 다른 경로의 결과는 반영하고 다음 질문으로 넘어간다.
        """

        answer = self._load_answer(state)
        message_text = state.get("message_text") or ""

        outbound = list(state.get("outbound", []))
        guide_message = self._build_guide_response(answer, message_text)
        if guide_message:
            outbound = self._emit(outbound, guide_message)

        self._extract_fraud_circumstances(answer, message_text)
        return {"outbound": outbound}

    def _finish(self, state: ChatGraphState) -> dict[str, Any]:
        """WANT_END — 채점을 집계한 뒤 상담사 연결로 넘긴다(PRD 2.6)."""

        circumstance_codes = [
            circumstance.circumstance_code
            for circumstance in self.repository.list_fraud_circumstances(
                self.chat_session
            )
        ]
        # 담당자가 화면에서 결과를 볼 수 있도록 전이보다 집계를 먼저 끝낸다.
        self.repository.add_fraud_type_scores(
            self.chat_session,
            type_scores=score_chat_fraud_circumstances(circumstance_codes),
        )
        self.repository.request_handoff(
            self.chat_session,
            completed_at=datetime.now(UTC),
        )
        # 상태 변경 SSE 발행은 7단계에서 이 지점에 붙인다(PRD 2.7).
        return {
            "outbound": self._emit(state.get("outbound", []), WANT_END_HANDOFF_MESSAGE),
        }

    # ------------------------------------------------------------------
    # 노드가 쓰는 보조 동작
    # ------------------------------------------------------------------

    def _build_guide_response(
        self,
        answer: ChatAnswer,
        message_text: str,
    ) -> str:
        """가이드 검색 질의를 분해·저장하고 RAG 응답 본문을 만든다."""

        try:
            extraction = self.guide_search_query_extractor.extract(
                user_answers=message_text
            )
        except ChatbotExtractionError:
            logger.warning(
                "가이드 검색 질의 분해를 건너뜁니다: session=%s",
                self.chat_session.chat_session_id,
            )
            return ""

        for position, query in enumerate(
            extraction.guide_search_queries,
            start=1,
        ):
            self.repository.add_guide_search_query(
                self.chat_session,
                position=position,
                title=query.title,
                search_query=query.search_query,
                evidence=query.evidence,
                source_answer=answer,
            )

        # 분해 결과가 없는 턴은 본문이 비므로 메시지를 보내지 않는다(PRD 2.5).
        if not extraction.guide_search_queries:
            return ""

        try:
            response = self.guide_responder.respond(
                guide_search_queries=extraction.guide_search_queries,
                session=self.session,
            )
        except Exception:
            logger.warning(
                "대응 가이드 응답 조립에 실패했습니다: session=%s",
                self.chat_session.chat_session_id,
            )
            return ""
        return response.message_text

    def _extract_fraud_circumstances(
        self,
        answer: ChatAnswer,
        message_text: str,
    ) -> None:
        """사기 정황을 추출해 세션당 enum 한 행으로 저장한다(PRD 2.6)."""

        try:
            extraction = self.fraud_circumstance_extractor.extract(
                user_answers=message_text
            )
        except ChatbotExtractionError:
            logger.warning(
                "사기 정황 추출을 건너뜁니다: session=%s",
                self.chat_session.chat_session_id,
            )
            return

        for circumstance in extraction.fraud_circumstances:
            self.repository.add_fraud_circumstance(
                self.chat_session,
                circumstance_code=circumstance.type,
                evidence=circumstance.evidence,
                source_answer=answer,
            )

    def _emit(self, outbound: list[str], message_text: str) -> list[str]:
        """챗봇 메시지를 대화 로그에 남기고 이번 턴 출력에 덧붙인다."""

        self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.AI,
            message_text=message_text,
        )
        return [*outbound, message_text]

    def _load_answer(self, state: ChatGraphState) -> ChatAnswer:
        answer_id = state.get("answer_id")
        answer = (
            self.session.get(ChatAnswer, answer_id)
            if answer_id is not None
            else None
        )
        if answer is None:
            raise ChatTurnRejectedError("평가된 답변을 찾을 수 없습니다")
        return answer

    def _last_verdict(self, state: ChatGraphState) -> str | None:
        answer_id = state.get("answer_id")
        if answer_id is None:
            return None
        answer = self.session.get(ChatAnswer, answer_id)
        return answer.quality_verdict if answer is not None else None

    def _load_transaction(self) -> Transaction:
        """B.1 치환에 쓰는 거래 원장을 읽는다."""

        transaction = self.session.get(
            Transaction,
            self.chat_session.transaction_id,
        )
        if transaction is None:
            raise ChatTurnRejectedError("세션에 연결된 거래를 찾을 수 없습니다")
        return transaction

    # ------------------------------------------------------------------
    # LLM 의존 서비스 지연 생성
    # ------------------------------------------------------------------

    @property
    def evaluator(self) -> AnswerEvaluator:
        if self._evaluator is None:
            self._evaluator = AnswerEvaluator()
        return self._evaluator

    @property
    def guide_search_query_extractor(self) -> GuideSearchQueryExtractor:
        if self._guide_search_query_extractor is None:
            self._guide_search_query_extractor = GuideSearchQueryExtractor()
        return self._guide_search_query_extractor

    @property
    def fraud_circumstance_extractor(self) -> FraudCircumstanceExtractor:
        if self._fraud_circumstance_extractor is None:
            self._fraud_circumstance_extractor = FraudCircumstanceExtractor()
        return self._fraud_circumstance_extractor

    @property
    def guide_responder(self) -> GuideResponder:
        if self._guide_responder is None:
            self._guide_responder = GuideResponder()
        return self._guide_responder


def _follow_route(state: ChatGraphState) -> str:
    """노드가 정한 다음 경로를 그대로 따른다."""

    return state["route"]


def _route_for_verdict(
    verdict: AnswerQualityVerdict,
    *,
    retry_exhausted: bool,
) -> str:
    """판정 5종을 그래프 경로로 옮긴다(PRD 2.4 조건 2 표)."""

    if verdict is AnswerQualityVerdict.WANT_END:
        return "finish"
    if verdict is AnswerQualityVerdict.SUFFICIENT:
        return "process_sufficient_answer"
    if verdict is AnswerQualityVerdict.REFUSAL:
        # 평가 LLM 실패도 REFUSAL 로 들어온다(README 2.4 평가 LLM 실패 시 동작).
        return "announce_next"
    # TOO_VAGUE / NON_ANSWER — 재질문 2회까지, 초과하면 채택하고 다음 질문으로.
    return "announce_next" if retry_exhausted else "reask"


__all__ = [
    "ChatGraphState",
    "ChatTurnRejectedError",
    "ChatTurnResult",
    "CustomerChatbotPipeline",
    "MAX_ATTEMPTS_PER_QUESTION",
]
