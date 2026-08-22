"""고객 챗봇의 유형 판별과 이후 자유 대화를 LangGraph로 처리한다."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import StreamWriter
from sqlmodel import Session

from app.data.model.chatbot import (
    ChatAnswer,
    ChatConversationPhase,
    ChatDiscriminationQuestionId,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.data.model.transaction import Transaction
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.dto.chatbot import (
    AnswerQualityVerdict,
    ChatDiscriminationAction,
    ChatUiEvent,
    DiscriminationQuestionId,
    ExtractedGuideSearchQuery,
)
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.answer_analyzer import AnswerAnalyzer
from app.services.chatbot.guide_responder import GuideResponder
from app.services.chatbot.messages import (
    FRAUD_DISCRIMINATION_FAILED_MESSAGE,
    FREE_CHAT_PROMPT,
    HANDOFF_FREE_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    NORMAL_GUIDE_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_MESSAGE,
    render_fraud_type_confirmed_message,
    render_initial_notification,
)
from app.services.chatbot.questions import (
    OWNERSHIP_QUESTION,
    predefined_guide_search_query,
    render_fraud_type_confirmation_question,
)


logger = logging.getLogger(__name__)

# Rule Engine 1·2위 점수 차이가 이 값 이상이면 확정 케이스다.
CONFIDENT_SCORE_MARGIN = 0.15
_CHECKPOINTER = InMemorySaver()


class ChatTurnRejectedError(RuntimeError):
    """현재 대화 단계에서 받을 수 없는 입력이 들어온 경우."""


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    """한 턴의 최종 출력."""

    messages: tuple[str, ...]
    status: ChatSessionStatus
    question_step: int
    conversation_phase: str | None
    question_id: str | None
    confirmed_fraud_type: str | None
    ui_events: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ChatTurnStreamEvent:
    """그래프가 턴 처리 중 SSE 라우터로 전달하는 이벤트."""

    event: str
    data: dict[str, Any]


class ChatGraphState(TypedDict, total=False):
    event: Literal[
        "INITIAL_NOTIFICATION",
        "DISCRIMINATION_ACTION",
        "CUSTOMER_MESSAGE",
    ]
    message_text: str | None
    discrimination_action: str | None
    submitted_question_id: str | None
    request_id: str | None
    question_step: int
    route: str
    next_question_id: str | None
    confirmed_fraud_type: str | None
    answer_id: int | None
    guide_search_queries: list[dict[str, str]]
    outbound: list[str]
    ui_events: list[dict[str, str]]
    stream_output: bool


class CustomerChatbotPipeline:
    """세션 생명주기와 대화 단계를 분리해 턴 단위로 커밋한다."""

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
        self._answer_analyzer = answer_analyzer
        self._guide_responder = guide_responder
        self.graph = self._build_graph(checkpointer or _CHECKPOINTER)

    # ------------------------------------------------------------------
    # 공개 진입점
    # ------------------------------------------------------------------

    def start_discrimination(self) -> ChatTurnResult:
        """본인 확인 직후 공통 안내와 첫 퀵리플라이 질문을 출력한다."""

        if self.chat_session.status != ChatSessionStatus.URL_SENT.value:
            raise ChatTurnRejectedError("이미 시작된 챗봇 세션입니다")
        return self._run_turn({"event": "INITIAL_NOTIFICATION"})

    def handle_discrimination_action_stream(
        self,
        *,
        action: ChatDiscriminationAction,
        question_id: DiscriminationQuestionId,
        request_id: str,
    ) -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
        """유형 판별 퀵리플라이를 처리하며 완료된 request_id는 재사용한다."""

        previous = self.repository.get_discrimination_action(
            self.chat_session,
            request_id=request_id,
        )
        if previous is not None:
            if previous.action != action.value or previous.question_id != question_id.value:
                raise ChatTurnRejectedError(
                    "같은 request_id를 다른 판별 액션에 사용할 수 없습니다"
                )
            return self._replay_discrimination_action(previous.response_payload)

        self._validate_discrimination_action(question_id)
        return self._stream_turn(
            {
                "event": "DISCRIMINATION_ACTION",
                "discrimination_action": action.value,
                "submitted_question_id": question_id.value,
                "request_id": request_id,
            }
        )

    def handle_message_stream(
        self,
        message_text: str,
    ) -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
        """대응가이드 이후 자유 질문을 AnswerAnalyzer → GuideResponder로 처리한다."""

        self._validate_message_turn()
        return self._stream_turn(
            {"event": "CUSTOMER_MESSAGE", "message_text": message_text}
        )

    def _validate_discrimination_action(
        self,
        question_id: DiscriminationQuestionId,
    ) -> None:
        if self.chat_session.conversation_phase != ChatConversationPhase.DISCRIMINATION.value:
            raise ChatTurnRejectedError(
                "네/아니요 판별 액션은 DISCRIMINATION 단계에서만 처리할 수 있습니다"
            )
        if self.chat_session.discrimination_question_id != question_id.value:
            raise ChatTurnRejectedError(
                "현재 질문과 일치하지 않는 늦은 퀵리플라이 응답입니다"
            )

    def _validate_message_turn(self) -> None:
        phase = self.chat_session.conversation_phase
        if phase == ChatConversationPhase.DISCRIMINATION.value:
            raise ChatTurnRejectedError(
                "DISCRIMINATION 단계에서는 네/아니요 퀵리플라이만 사용할 수 있습니다"
            )
        if phase not in {
            ChatConversationPhase.FREE_CHAT.value,
            ChatConversationPhase.HANDOFF_PENDING.value,
        }:
            raise ChatTurnRejectedError("현재 단계에서는 자유 텍스트를 받을 수 없습니다")
        if (
            phase == ChatConversationPhase.FREE_CHAT.value
            and self.chat_session.status != ChatSessionStatus.IN_PROGRESS.value
        ):
            raise ChatTurnRejectedError("종료된 챗봇 세션입니다")

    # ------------------------------------------------------------------
    # 턴 실행
    # ------------------------------------------------------------------

    def _run_turn(self, turn_input: dict[str, Any]) -> ChatTurnResult:
        config = self._turn_config()
        state_input = self._turn_state_input(turn_input, stream_output=False)
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
        config = self._turn_config()
        state_input = self._turn_state_input(turn_input, stream_output=True)
        final_state: dict[str, Any] | None = None
        try:
            for stream_mode, value in self.graph.stream(
                state_input,
                config=config,
                stream_mode=["custom", "values"],
            ):
                if stream_mode == "custom":
                    yield ChatTurnStreamEvent(event=value["event"], data=value["data"])
                elif stream_mode == "values":
                    final_state = value
            if final_state is None:
                raise RuntimeError("챗봇 그래프가 최종 상태를 반환하지 않았습니다")
            self._commit_turn(final_state)
            yield self._turn_result(final_state)
        except BaseException:
            self.session.rollback()
            raise

    def _turn_config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.chat_session.chat_session_id}}

    def _turn_state_input(
        self,
        turn_input: dict[str, Any],
        *,
        stream_output: bool,
    ) -> dict[str, Any]:
        return {
            "message_text": None,
            "discrimination_action": None,
            "submitted_question_id": None,
            "request_id": None,
            "question_step": self.chat_session.question_step,
            "route": "",
            "next_question_id": None,
            "confirmed_fraud_type": None,
            "answer_id": None,
            "guide_search_queries": [],
            "outbound": [],
            "ui_events": [],
            "stream_output": stream_output,
            **turn_input,
        }

    def _commit_turn(self, final_state: dict[str, Any]) -> None:
        self.repository.update_question_step(
            self.chat_session,
            final_state.get("question_step", self.chat_session.question_step),
        )
        if final_state.get("event") == "DISCRIMINATION_ACTION":
            self.repository.add_discrimination_action(
                self.chat_session,
                request_id=final_state["request_id"],
                question_id=final_state["submitted_question_id"],
                action=final_state["discrimination_action"],
                response_payload=self._result_payload(final_state),
            )
        self.session.commit()
        self.session.refresh(self.chat_session)

    def _turn_result(self, final_state: dict[str, Any]) -> ChatTurnResult:
        return ChatTurnResult(
            messages=tuple(final_state.get("outbound", [])),
            status=ChatSessionStatus(self.chat_session.status),
            question_step=self.chat_session.question_step,
            conversation_phase=self.chat_session.conversation_phase,
            question_id=self.chat_session.discrimination_question_id,
            confirmed_fraud_type=self.chat_session.confirmed_fraud_type,
            ui_events=tuple(final_state.get("ui_events", [])),
        )

    def _result_payload(self, final_state: dict[str, Any]) -> dict[str, object]:
        result = self._turn_result(final_state)
        return {
            "messages": list(result.messages),
            "status": result.status.value,
            "question_step": result.question_step,
            "conversation_phase": result.conversation_phase,
            "question_id": result.question_id,
            "confirmed_fraud_type": result.confirmed_fraud_type,
            "ui_events": list(result.ui_events),
        }

    @staticmethod
    def _replay_discrimination_action(
        payload: dict[str, object],
    ) -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
        def replay() -> Iterator[ChatTurnStreamEvent | ChatTurnResult]:
            ui_events = tuple(payload.get("ui_events", []))
            for item in ui_events:
                if isinstance(item, dict):
                    yield ChatTurnStreamEvent(event=item["event"], data=dict(item))
            yield ChatTurnResult(
                messages=tuple(payload.get("messages", [])),
                status=ChatSessionStatus(str(payload["status"])),
                question_step=int(payload["question_step"]),
                conversation_phase=(
                    str(payload["conversation_phase"])
                    if payload.get("conversation_phase") is not None
                    else None
                ),
                question_id=(
                    str(payload["question_id"])
                    if payload.get("question_id") is not None
                    else None
                ),
                confirmed_fraud_type=(
                    str(payload["confirmed_fraud_type"])
                    if payload.get("confirmed_fraud_type") is not None
                    else None
                ),
                ui_events=tuple(item for item in ui_events if isinstance(item, dict)),
            )

        return replay()

    # ------------------------------------------------------------------
    # 그래프
    # ------------------------------------------------------------------

    def _build_graph(self, checkpointer: Any) -> Any:
        builder = StateGraph(ChatGraphState)
        builder.add_node("initialize", self._initialize)
        builder.add_node("discriminate", self._discriminate)
        builder.add_node("ask_discrimination", self._ask_discrimination)
        builder.add_node("confirm_and_guide", self._confirm_and_guide)
        builder.add_node("normal_guide", self._normal_guide)
        builder.add_node("handoff", self._handoff)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("reask", self._reask)
        builder.add_node("process_sufficient_answer", self._process_sufficient_answer)
        builder.add_node("finish", self._finish)

        builder.set_conditional_entry_point(
            lambda state: state["event"],
            {
                "INITIAL_NOTIFICATION": "initialize",
                "DISCRIMINATION_ACTION": "discriminate",
                "CUSTOMER_MESSAGE": "evaluate",
            },
        )
        builder.add_conditional_edges(
            "initialize", self._follow_route, {"end": END, "handoff": "handoff"}
        )
        builder.add_conditional_edges(
            "discriminate",
            self._follow_route,
            {
                "ask_discrimination": "ask_discrimination",
                "confirm_and_guide": "confirm_and_guide",
                "normal_guide": "normal_guide",
                "handoff": "handoff",
            },
        )
        builder.add_edge("ask_discrimination", END)
        builder.add_edge("confirm_and_guide", END)
        builder.add_edge("normal_guide", END)
        builder.add_edge("handoff", END)
        builder.add_conditional_edges(
            "evaluate",
            self._follow_route,
            {
                "process_sufficient_answer": "process_sufficient_answer",
                "reask": "reask",
                "finish": "finish",
            },
        )
        builder.add_edge("process_sufficient_answer", END)
        builder.add_edge("reask", END)
        builder.add_edge("finish", END)
        return builder.compile(checkpointer=checkpointer)

    @staticmethod
    def _follow_route(state: ChatGraphState) -> str:
        return state["route"]

    # ------------------------------------------------------------------
    # 판별 노드
    # ------------------------------------------------------------------

    def _initialize(self, state: ChatGraphState) -> dict[str, Any]:
        transaction = self._load_transaction()
        self.repository.update_status(self.chat_session, ChatSessionStatus.IN_PROGRESS)
        self.chat_session.conversation_phase = ChatConversationPhase.DISCRIMINATION.value
        self.chat_session.discrimination_question_id = (
            ChatDiscriminationQuestionId.OWNERSHIP.value
        )
        outbound = self._emit(
            state.get("outbound", []),
            render_initial_notification(
                transaction_datetime=transaction.transaction_datetime,
                transaction_amount=transaction.transaction_amount,
            ),
        )
        if not self._has_valid_type_candidates():
            return {"outbound": outbound, "route": "handoff"}
        return {
            "outbound": self._emit(outbound, OWNERSHIP_QUESTION),
            "route": "end",
        }

    def _discriminate(self, state: ChatGraphState) -> dict[str, Any]:
        action = state["discrimination_action"]
        question_id = state["submitted_question_id"]
        answer_text = "네" if action == ChatDiscriminationAction.ANSWER_YES.value else "아니요"
        self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text=answer_text,
        )

        is_yes = action == ChatDiscriminationAction.ANSWER_YES.value
        is_confident = self._is_confident_case()
        primary_type, secondary_type = self.chat_session.top_fraud_types or (None, None)

        if question_id == DiscriminationQuestionId.OWNERSHIP.value:
            self.chat_session.ownership_answer = action
            if is_confident and not is_yes:
                return {"confirmed_fraud_type": primary_type, "route": "confirm_and_guide"}
            return {
                "next_question_id": DiscriminationQuestionId.PRIMARY_CHECK.value,
                "route": "ask_discrimination",
            }

        if question_id == DiscriminationQuestionId.PRIMARY_CHECK.value:
            if is_yes:
                return {"confirmed_fraud_type": primary_type, "route": "confirm_and_guide"}
            if is_confident:
                return {"route": "normal_guide"}
            return {
                "next_question_id": DiscriminationQuestionId.SECONDARY_CHECK.value,
                "route": "ask_discrimination",
            }

        if question_id == DiscriminationQuestionId.SECONDARY_CHECK.value:
            if is_yes:
                return {"confirmed_fraud_type": secondary_type, "route": "confirm_and_guide"}
            return {
                "route": (
                    "normal_guide"
                    if self.chat_session.ownership_answer
                    == ChatDiscriminationAction.ANSWER_YES.value
                    else "handoff"
                )
            }
        raise ChatTurnRejectedError(f"알 수 없는 판별 질문입니다: {question_id}")

    def _ask_discrimination(self, state: ChatGraphState) -> dict[str, Any]:
        question_id = state["next_question_id"]
        if question_id == DiscriminationQuestionId.PRIMARY_CHECK.value:
            fraud_type = self.chat_session.top_fraud_types[0]
        elif question_id == DiscriminationQuestionId.SECONDARY_CHECK.value:
            fraud_type = self.chat_session.top_fraud_types[1]
        else:
            raise ChatTurnRejectedError(f"알 수 없는 다음 질문입니다: {question_id}")
        self.chat_session.discrimination_question_id = question_id
        return {
            "outbound": self._emit(
                state.get("outbound", []),
                render_fraud_type_confirmation_question(fraud_type),
            )
        }

    def _confirm_and_guide(
        self,
        state: ChatGraphState,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        fraud_type = state["confirmed_fraud_type"]
        if fraud_type not in FINAL_FRAUD_TYPE_CODES:
            raise ChatTurnRejectedError("확정된 사기유형이 올바르지 않습니다")
        self.chat_session.confirmed_fraud_type = fraud_type
        self.chat_session.conversation_phase = ChatConversationPhase.FREE_CHAT.value
        self.chat_session.discrimination_question_id = None

        popup = ChatUiEvent(
            event="fraud_type_confirmed",
            confirmed_fraud_type=fraud_type,
            message=render_fraud_type_confirmed_message(fraud_type),
        ).model_dump(mode="json")
        # RAG 검색·생성을 시작하기 전에 팝업 이벤트를 먼저 전송한다.
        if state.get("stream_output"):
            writer({"event": popup["event"], "data": popup})

        outbound = list(state.get("outbound", []))
        guide_message = self._respond_to_queries(
            [predefined_guide_search_query(fraud_type)],
            writer=writer,
            stream_output=bool(state.get("stream_output")),
            persist_queries=False,
        )
        if guide_message:
            outbound = self._emit(outbound, guide_message)
        outbound = self._emit(outbound, FREE_CHAT_PROMPT)
        return {
            "question_step": max(1, state.get("question_step", 0)),
            "outbound": outbound,
            "ui_events": [popup],
        }

    def _normal_guide(self, state: ChatGraphState) -> dict[str, Any]:
        self.chat_session.conversation_phase = ChatConversationPhase.NORMAL_GUIDE.value
        self.chat_session.discrimination_question_id = None
        self.repository.set_session_complete(
            self.chat_session,
            completed_at=datetime.now(UTC),
        )
        return {"outbound": self._emit(state.get("outbound", []), NORMAL_GUIDE_MESSAGE)}

    def _handoff(self, state: ChatGraphState) -> dict[str, Any]:
        self.repository.request_handoff(self.chat_session)
        self.chat_session.conversation_phase = ChatConversationPhase.HANDOFF_PENDING.value
        self.chat_session.discrimination_question_id = None
        outbound = self._emit(
            state.get("outbound", []), FRAUD_DISCRIMINATION_FAILED_MESSAGE
        )
        outbound = self._emit(outbound, HANDOFF_WAITING_MESSAGE)
        outbound = self._emit(outbound, HANDOFF_FREE_CHAT_MESSAGE)
        return {
            "question_step": max(1, state.get("question_step", 0)),
            "outbound": outbound,
        }

    # ------------------------------------------------------------------
    # 자유 대화 노드
    # ------------------------------------------------------------------

    def _evaluate(self, state: ChatGraphState) -> dict[str, Any]:
        message_text = state.get("message_text") or ""
        question_step = state.get("question_step", 0) + 1
        message = self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text=message_text,
        )
        outcome = self.answer_analyzer.analyze(
            question_text=FREE_CHAT_PROMPT,
            customer_answer=message_text,
        )
        verdict = outcome.quality_verdict
        is_adopted = verdict is AnswerQualityVerdict.SUFFICIENT
        answer = self.repository.add_answer(
            self.chat_session,
            message=message,
            question_step=question_step,
            attempt_no=1,
            quality_verdict=verdict,
            verdict_skip_reason=outcome.verdict_skip_reason,
            is_adopted=is_adopted,
        )
        self.session.flush()

        if verdict is AnswerQualityVerdict.SUFFICIENT:
            route = "process_sufficient_answer"
        elif verdict is AnswerQualityVerdict.WANT_END:
            route = "finish"
        else:
            route = "reask"
        return {
            "question_step": question_step,
            "answer_id": answer.answer_id,
            "guide_search_queries": [
                query.model_dump() for query in outcome.guide_search_queries
            ]
            if verdict is AnswerQualityVerdict.SUFFICIENT
            else [],
            "route": route,
        }

    def _reask(self, state: ChatGraphState) -> dict[str, Any]:
        return {"outbound": self._emit(state.get("outbound", []), TOO_VAGUE_MESSAGE)}

    def _process_sufficient_answer(
        self,
        state: ChatGraphState,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        answer = self._load_answer(state)
        queries = [
            ExtractedGuideSearchQuery.model_validate(query)
            for query in state.get("guide_search_queries", [])
        ]
        for position, query in enumerate(queries, start=1):
            self.repository.add_guide_search_query(
                self.chat_session,
                position=position,
                title=query.title,
                search_query=query.search_query,
                evidence=query.evidence,
                source_answer=answer,
            )
        guide_message = self._respond_to_queries(
            queries,
            writer=writer,
            stream_output=bool(state.get("stream_output")),
            persist_queries=True,
        )
        outbound = list(state.get("outbound", []))
        if guide_message:
            outbound = self._emit(outbound, guide_message)
        return {"outbound": outbound}

    def _finish(self, state: ChatGraphState) -> dict[str, Any]:
        # 상담사 요청은 고객이 자유 대화를 끝내도 담당자에게 계속 노출한다.
        if self.chat_session.conversation_phase != ChatConversationPhase.HANDOFF_PENDING.value:
            self.repository.set_session_complete(
                self.chat_session,
                completed_at=datetime.now(UTC),
            )
        return {"outbound": self._emit(state.get("outbound", []), WANT_END_MESSAGE)}

    # ------------------------------------------------------------------
    # 보조 동작
    # ------------------------------------------------------------------

    def _respond_to_queries(
        self,
        queries: list[ExtractedGuideSearchQuery],
        *,
        writer: StreamWriter,
        stream_output: bool,
        persist_queries: bool,
    ) -> str:
        del persist_queries  # 호출부에서 시스템 질의 저장 금지를 명시적으로 드러낸다.
        if not queries:
            return ""
        on_snapshot: Callable[[str], None] | None = None
        if stream_output:
            on_snapshot = lambda message_text: writer(
                {
                    "event": "chat_message_snapshot",
                    "data": {"message_index": 0, "message_text": message_text},
                }
            )
        try:
            kwargs: dict[str, Any] = {
                "guide_search_queries": queries,
                "session": self.session,
            }
            if on_snapshot is not None:
                kwargs["on_snapshot"] = on_snapshot
            return self.guide_responder.respond(**kwargs).message_text
        except Exception:
            logger.warning(
                "대응 가이드 응답 조립에 실패했습니다: session=%s",
                self.chat_session.chat_session_id,
            )
            return ""

    def _has_valid_type_candidates(self) -> bool:
        types = self.chat_session.top_fraud_types
        scores = self.chat_session.top_fraud_type_scores
        if types is None or len(types) != 2 or types[0] == types[1]:
            return False
        if any(item not in FINAL_FRAUD_TYPE_CODES for item in types):
            return False
        if scores is None:
            return False
        return all(
            isinstance(scores.get(item), (int, float))
            and not isinstance(scores.get(item), bool)
            and 0.0 <= float(scores[item]) <= 1.0
            for item in types
        )

    def _is_confident_case(self) -> bool:
        if not self._has_valid_type_candidates():
            return False
        primary, secondary = self.chat_session.top_fraud_types
        scores = self.chat_session.top_fraud_type_scores
        score_margin = round(
            float(scores[primary]) - float(scores[secondary]),
            10,
        )
        return score_margin >= CONFIDENT_SCORE_MARGIN

    def _emit(self, outbound: list[str], message_text: str) -> list[str]:
        self.repository.add_message(
            self.chat_session,
            sender_type=ChatSenderType.AI,
            message_text=message_text,
        )
        return [*outbound, message_text]

    def _load_answer(self, state: ChatGraphState) -> ChatAnswer:
        answer_id = state.get("answer_id")
        answer = self.session.get(ChatAnswer, answer_id) if answer_id is not None else None
        if answer is None:
            raise ChatTurnRejectedError("평가된 답변을 찾을 수 없습니다")
        return answer

    def _load_transaction(self) -> Transaction:
        transaction = self.session.get(Transaction, self.chat_session.transaction_id)
        if transaction is None:
            raise ChatTurnRejectedError("세션에 연결된 거래를 찾을 수 없습니다")
        return transaction

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


__all__ = [
    "CONFIDENT_SCORE_MARGIN",
    "ChatGraphState",
    "ChatTurnRejectedError",
    "ChatTurnResult",
    "ChatTurnStreamEvent",
    "CustomerChatbotPipeline",
]
