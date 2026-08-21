"""고객 입력 한 건을 LangGraph 턴 하나로 처리한다.

질문 단계와 재시도 횟수는 세션별 체크포인트에 저장한다. 사기 정황 추출은 턴 커밋 후
실행할 작업으로 반환한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Callable, Iterator
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import StreamWriter
from sqlmodel import Session

from app.data.model.chatbot import (
    ChatAnswer,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.data.model.transaction import Transaction
from app.dto.chatbot import (
    AnswerQualityVerdict,
    ChatButtonAction,
    ExtractedGuideSearchQuery,
    FraudCircumstanceExtractionTask,
)
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.answer_analyzer import AnswerAnalyzer
from app.services.chatbot.chat_scoring import rescore_chat_session
from app.services.chatbot.guide_responder import GuideResponder
from app.services.chatbot.messages import (
    END_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    NEXT_QUESTION_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_MESSAGE,
    render_initial_notification,
)
from app.services.chatbot.questions import render_question


logger = logging.getLogger(__name__)


# 최초 답변 1회와 재질문 2회를 허용한다.
MAX_ATTEMPTS_PER_QUESTION = 3

# 프로세스 전역 체크포인터. 턴과 턴 사이의 진행 상태는 여기에만 있다.
_CHECKPOINTER = InMemorySaver()


class ChatTurnRejectedError(RuntimeError):
    """현재 세션 상태에서 받을 수 없는 입력이 들어온 경우."""


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    """한 턴의 출력과 커밋 후 실행할 사기 정황 추출 작업."""

    messages: tuple[str, ...]
    status: ChatSessionStatus
    question_step: int
    pending_extraction: FraudCircumstanceExtractionTask | None = None


@dataclass(frozen=True, slots=True)
class ChatTurnStreamEvent:
    """그래프가 턴 처리 중 라우터로 전달하는 SSE 이벤트."""

    event: str
    data: dict[str, Any]


class ChatGraphState(TypedDict, total=False):
    """체크포인트에 저장되는 그래프 상태."""

    # 이번 턴의 입력
    event: Literal[
        "INITIAL_NOTIFICATION",
        "BUTTON_ACTION",
        "CUSTOMER_MESSAGE",
    ]
    button_action: str | None  # event가 BUTTON_ACTION일 때만 사용
    message_text: str | None
    question_step: int  # 턴 사이에 유지되는 진행 상태
    attempt_no: int  # 현재 시도 횟수
    # 턴 안에서만 쓰는 값
    route: str  # 다음 경로의 위치
    answer_id: int | None
    # Pydantic 객체 대신 직렬화 가능한 dict를 저장한다.
    guide_search_queries: list[dict[str, str]]
    outbound: list[str]
    # 턴 커밋 뒤 백그라운드 추출에 넘길 답변 id.
    pending_extraction_answer_id: int | None
    # 메시지 API에서만 가이드 생성 중간 스냅샷을 custom stream으로 내보낸다.
    stream_output: bool


class CustomerChatbotPipeline:
    """세션 하나의 상담 흐름을 조정하고 턴 단위로 커밋한다."""

    def __init__(
        self,
        *,
        session: Session,
        chat_session: ChatSession,
        answer_analyzer: AnswerAnalyzer | None = None,
        guide_responder: GuideResponder | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.session = session
        self.chat_session = chat_session
        self.repository = ChatSessionRepository(session)
        # LLM이 필요 없는 턴에서는 클라이언트를 생성하지 않는다.
        self._answer_analyzer = answer_analyzer
        self._guide_responder = guide_responder
        self.graph = self._build_graph(checkpointer or _CHECKPOINTER)

    # ------------------------------------------------------------------
    # 공개 진입점
    # ------------------------------------------------------------------

    def send_initial_notification(self) -> ChatTurnResult:
        """최초 알림을 출력한다. 세션 상태는 변경하지 않는다."""

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

        self._validate_message_turn()
        return self._run_turn(
            {"event": "CUSTOMER_MESSAGE", "message_text": message_text}
        )

    def handle_message_stream(
        self,
        message_text: str,
    ) -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
        """고객 답변 턴의 가이드 스냅샷과 최종 결과를 반환한다."""

        self._validate_message_turn()
        return self._stream_turn(
            {"event": "CUSTOMER_MESSAGE", "message_text": message_text}
        )

    def _validate_message_turn(self) -> None:
        """현재 세션이 고객 답변을 받을 수 있는지 확인한다."""

        if self.chat_session.status != ChatSessionStatus.IN_PROGRESS.value:
            raise ChatTurnRejectedError(
                "고객 답변은 IN_PROGRESS 상태에서만 처리할 수 있습니다"
            )
        if self.chat_session.question_step < 1:
            raise ChatTurnRejectedError("답변을 기다리는 질문이 없습니다")

    # ------------------------------------------------------------------
    # 턴 실행과 트랜잭션 소유
    # ------------------------------------------------------------------

    def _run_turn(self, turn_input: dict[str, Any]) -> ChatTurnResult:
        """챗봇 그래프 한 주기"""

        config = self._turn_config()
        state_input = self._turn_state_input(
            turn_input,
            config=config,
            stream_output=False,
        )

        try:
            final_state = self.graph.invoke(state_input, config=config)
            self._commit_turn(final_state)
        except Exception:
            self.session.rollback()
            raise

        return self._turn_result(final_state)

    def _stream_turn(
        self,
        turn_input: dict[str, Any],
    ) -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
        """LangGraph custom stream을 전달하고 커밋 뒤 최종 결과를 마지막에 보낸다."""

        config = self._turn_config()
        state_input = self._turn_state_input(
            turn_input,
            config=config,
            stream_output=True,
        )
        final_state: dict[str, Any] | None = None

        try:
            for stream_mode, value in self.graph.stream(
                state_input,
                config=config,
                stream_mode=["custom", "values"],
            ):
                if stream_mode == "custom":
                    yield ChatTurnStreamEvent(
                        event=value["event"],
                        data=value["data"],
                    )
                elif stream_mode == "values":
                    final_state = value

            if final_state is None:
                raise RuntimeError("챗봇 그래프가 최종 상태를 반환하지 않았습니다")

            self._commit_turn(final_state)
            # 최종 결과는 DB 커밋과 refresh가 모두 끝난 다음에만 내보낸다.
            yield self._turn_result(final_state)
        except BaseException:
            self.session.rollback()
            raise

    def _turn_config(self) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": self.chat_session.chat_session_id}
        }

    def _turn_state_input(
        self,
        turn_input: dict[str, Any],
        *,
        config: dict[str, Any],
        stream_output: bool,
    ) -> dict[str, Any]:
        # 리듀서를 두지 않으므로 턴 전용 값은 매번 명시적으로 이전 값을 덮는다.
        state_input: dict[str, Any] = {
            "button_action": None,
            "message_text": None,
            "answer_id": None,
            "guide_search_queries": [],
            "outbound": [],
            "pending_extraction_answer_id": None,
            "stream_output": stream_output,
            **turn_input,
        }
        state_input.update(self._seed_progress_state(config))
        return state_input

    def _commit_turn(self, final_state: dict[str, Any]) -> None:
        self.repository.update_question_step(
            self.chat_session,
            final_state.get("question_step", 0),
        )
        self.session.commit()
        self.session.refresh(self.chat_session)

    def _turn_result(self, final_state: dict[str, Any]) -> ChatTurnResult:
        return ChatTurnResult(
            messages=tuple(final_state.get("outbound", [])),
            status=ChatSessionStatus(self.chat_session.status),
            question_step=self.chat_session.question_step,
            pending_extraction=self._pending_extraction(final_state),
        )

    def _pending_extraction(
        self,
        final_state: dict[str, Any],
    ) -> FraudCircumstanceExtractionTask | None:
        """이번 턴이 예약한 사기 정황 추출 작업을 만든다(없으면 ``None``)."""

        answer_id = final_state.get("pending_extraction_answer_id")
        if answer_id is None:
            return None
        return FraudCircumstanceExtractionTask(
            chat_session_id=self.chat_session.chat_session_id,
            transaction_id=self.chat_session.transaction_id,
            answer_id=answer_id,
            message_text=final_state.get("message_text") or "",
        )

    def _seed_progress_state(self, config: dict[str, Any]) -> dict[str, Any]:
        """체크포인트가 없으면 DB의 질문 단계와 초기 시도 횟수를 사용한다."""

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
        # 채택 답변과 전환 안내 뒤에는 다음 질문을 보낸다.
        builder.add_edge("process_sufficient_answer", "ask_question")
        builder.add_edge("announce_next", "ask_question")
        builder.add_edge("finish", END)
        return builder.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------
    # 노드
    # ------------------------------------------------------------------

    def _notify(self, state: ChatGraphState) -> dict[str, Any]:
        """거래 정보를 포함한 최초 알림을 만든다."""

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
        """상담 시작·상담사 연결·종료 액션을 처리한다."""

        action = state.get("button_action")
        if action == ChatButtonAction.START_CHAT.value:
            self.repository.update_status(
                self.chat_session,
                ChatSessionStatus.IN_PROGRESS,
            )
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
        """고객 답변을 저장·분석하고 다음 경로를 정한다."""

        message_text = state.get("message_text") or ""
        question_step = state["question_step"]
        attempt_no = min(
            state.get("attempt_no", 0) + 1,
            MAX_ATTEMPTS_PER_QUESTION,
        )

        message = self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text=message_text,
        )
        outcome = self.answer_analyzer.analyze(
            question_text=render_question(
                question_step=question_step,
                top_fraud_types=self.chat_session.top_fraud_types,
            ),
            customer_answer=message_text,
        )
        verdict = outcome.quality_verdict
        if verdict is None:
            # 평가 장애는 고객 판정이 아니며, 미채택으로 저장한 뒤 다음 질문으로 간다.
            retry_exhausted = False
            is_adopted = False
            route = "announce_next"
        else:
            # TOO_VAGUE가 세 번째 응답까지 이어지면 재질문을 중단하고,
            # 마지막 응답을 채택한 뒤 추출 없이 다음 질문으로 넘어간다.
            retry_exhausted = (
                verdict is AnswerQualityVerdict.TOO_VAGUE
                and attempt_no >= MAX_ATTEMPTS_PER_QUESTION
            )
            is_adopted = (
                verdict is AnswerQualityVerdict.SUFFICIENT or retry_exhausted
            )
            route = _route_for_verdict(
                verdict,
                retry_exhausted=retry_exhausted,
            )

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

        return {
            "attempt_no": attempt_no,
            "answer_id": answer.answer_id,
            "guide_search_queries": [
                query.model_dump() for query in outcome.guide_search_queries
            ]
            if verdict is AnswerQualityVerdict.SUFFICIENT
            else [],
            "route": route,
        }

    def _reask(self, state: ChatGraphState) -> dict[str, Any]:
        """질문 단계를 유지하고 재질문 안내를 출력한다."""

        return {
            "outbound": self._emit(
                state.get("outbound", []),
                TOO_VAGUE_MESSAGE,
            )
        }

    def _announce_next(self, state: ChatGraphState) -> dict[str, Any]:
        """재시도 소진 또는 분석 장애 후 다음 질문 전환을 알린다."""

        return {"outbound": self._emit(state.get("outbound", []), NEXT_QUESTION_MESSAGE)}

    def _process_sufficient_answer(
        self,
        state: ChatGraphState,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """채택 답변의 가이드를 만들고 커밋 후 실행할 추출 작업을 예약한다."""

        answer = self._load_answer(state)
        guide_search_queries = [
            ExtractedGuideSearchQuery.model_validate(query)
            for query in state.get("guide_search_queries", [])
        ]

        outbound = list(state.get("outbound", []))
        on_snapshot: Callable[[str], None] | None = None
        if state.get("stream_output"):
            on_snapshot = lambda message_text: writer(
                {
                    "event": "chat_message_snapshot",
                    "data": {
                        "message_index": 0,
                        "message_text": message_text,
                    },
                }
            )

        guide_message = self._build_guide_response(
            answer,
            guide_search_queries,
            on_snapshot=on_snapshot,
        )
        if guide_message:
            outbound = self._emit(outbound, guide_message)

        return {
            "outbound": outbound,
            "pending_extraction_answer_id": answer.answer_id,
        }

    def _finish(self, state: ChatGraphState) -> dict[str, Any]:
        """최종 점수를 저장하고 상담을 종료한다."""

        self._update_fraud_type_scores()
        self.repository.set_session_complete(
            self.chat_session,
            completed_at=datetime.now(UTC),
        )
        return {
            "outbound": self._emit(state.get("outbound", []), WANT_END_MESSAGE),
        }

    # ------------------------------------------------------------------
    # 노드가 쓰는 보조 동작
    # ------------------------------------------------------------------

    def _build_guide_response(
        self,
        answer: ChatAnswer,
        guide_search_queries: list[ExtractedGuideSearchQuery],
        *,
        on_snapshot: Callable[[str], None] | None = None,
    ) -> str:
        """통합 분석에서 받은 가이드 검색 질의를 저장하고 RAG 응답을 만든다."""

        for position, query in enumerate(
            guide_search_queries,
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

        # 검색 질의가 없으면 가이드 메시지를 만들지 않는다.
        if not guide_search_queries:
            return ""

        try:
            respond_kwargs: dict[str, Any] = {
                "guide_search_queries": guide_search_queries,
                "session": self.session,
            }
            if on_snapshot is not None:
                respond_kwargs["on_snapshot"] = on_snapshot
            response = self.guide_responder.respond(**respond_kwargs)
        except Exception:
            logger.warning(
                "대응 가이드 응답 조립에 실패했습니다: session=%s",
                self.chat_session.chat_session_id,
            )
            return ""
        return response.message_text

    def _update_fraud_type_scores(self) -> None:
        """세션의 사기 정황 전체로 유형별 점수를 갱신한다."""

        rescore_chat_session(self.repository, self.chat_session)

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

    def _load_transaction(self) -> Transaction:
        """최초 알림에 사용할 거래를 조회한다."""

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
    def answer_analyzer(self) -> AnswerAnalyzer:
        if self._answer_analyzer is None:
            self._answer_analyzer = AnswerAnalyzer()
        return self._answer_analyzer

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
    """답변 판정을 그래프 경로로 변환한다."""

    if verdict is AnswerQualityVerdict.WANT_END:
        return "finish"
    if verdict is AnswerQualityVerdict.SUFFICIENT:
        return "process_sufficient_answer"
    # TOO_VAGUE — 재질문 2회까지, 초과하면 채택하고 다음 질문으로.
    return "announce_next" if retry_exhausted else "reask"


__all__ = [
    "ChatGraphState",
    "ChatTurnRejectedError",
    "ChatTurnResult",
    "ChatTurnStreamEvent",
    "CustomerChatbotPipeline",
    "MAX_ATTEMPTS_PER_QUESTION",
]
