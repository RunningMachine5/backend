"""고객 대응 챗봇의 API 및 LLM 구조화 출력 계약."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


FraudTypeCode = Literal[*FINAL_FRAUD_TYPE_CODES]
ChatSessionStatusValue = Literal[
    "URL_SENT",
    "IN_PROGRESS",
    "HANDOFF_REQUESTED",
    "DONE",
    "FAILED",
]
ChatConversationPhaseValue = Literal[
    "DISCRIMINATION",
    "FREE_CHAT",
    "HANDOFF_PENDING",
    "NORMAL_GUIDE",
]
ChatInputMode = Literal["QUICK_REPLY", "FREE_TEXT", "NONE"]

class CreateChatRequest(BaseModel):
    """거래에 연결할 고객 채팅 세션 생성 입력."""

    # transactions.id 와 chat_sessions.transaction_id 는 DB가 발급하는 BIGINT 다.
    transaction_id: int = Field(gt=0)
    # 룰 채점 점수 내림차순 상위 2개 사기유형. 1·2순위 확인 질문에 쓴다.
    # 유형 점수가 없으면 생략할 수 있으며, 해당 세션은 상담사 연결로 전환한다.
    top_fraud_types: list[FraudTypeCode] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )

    @field_validator("top_fraud_types")
    @classmethod
    def _reject_duplicate_top_fraud_types(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        """상위 1·2위가 같은 유형이면 유형판별 질문을 고를 수 없으므로 거부한다."""

        if value is not None and value[0] == value[1]:
            raise ValueError("top_fraud_types의 두 사기유형은 서로 달라야 합니다")
        return value

class AnswerQualityVerdict(StrEnum):
    """고객 답변 충실도 평가 결과."""

    SUFFICIENT = "SUFFICIENT"
    TOO_VAGUE = "TOO_VAGUE"
    WANT_END = "WANT_END"


class ExtractedGuideSearchQuery(BaseModel):
    """고객 답변에서 분해한 독립 검색 단위와 원문 근거."""

    title: str = Field(min_length=1, max_length=120)
    search_query: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1)


class AnswerAnalysisResult(BaseModel):
    """답변 판정과 가이드 검색 질의 분해를 합친 LLM 구조화 출력."""

    verdict: AnswerQualityVerdict
    guide_search_queries: list[ExtractedGuideSearchQuery] = Field(max_length=5)


class ChatDiscriminationAction(StrEnum):
    """유형 판별 질문의 네/아니요 퀵리플라이 액션."""

    ANSWER_YES = "ANSWER_YES"
    ANSWER_NO = "ANSWER_NO"


class DiscriminationQuestionId(StrEnum):
    """현재 답변하고 있는 유형 판별 질문."""

    OWNERSHIP = "OWNERSHIP"
    PRIMARY_CHECK = "PRIMARY_CHECK"
    SECONDARY_CHECK = "SECONDARY_CHECK"


class ChatDiscriminationActionRequest(BaseModel):
    """유형 판별 퀵리플라이 입력과 멱등 키."""

    action: ChatDiscriminationAction
    question_id: DiscriminationQuestionId
    request_id: str = Field(min_length=1, max_length=64)


class ChatQuickReplyResponse(BaseModel):
    """프론트가 현재 질문에 표시할 퀵리플라이 버튼."""

    label: str
    action: ChatDiscriminationAction


class ChatUiEvent(BaseModel):
    """대화 메시지와 별도로 프론트 컴포넌트를 구동하는 이벤트."""

    event: Literal["fraud_type_confirmed"]
    confirmed_fraud_type: FraudTypeCode
    message: str

class SendChatMessageRequest(BaseModel):
    """고객이 질문에 답한 메시지 한 건."""

    message_text: str = Field(
        min_length=1,
        description="고객이 입력한 답변 원문. 빈 문자열은 받지 않는다.",
    )

class ChatMessageResponse(BaseModel):
    """대화 이력에 쌓인 메시지 한 건."""

    message_id: int = Field(gt=0, description="메시지 id. 이력 정렬 기준이다.")
    sender_type: Literal["AI", "HUMAN", "SYSTEM"] = Field(
        description="작성 주체. `AI`(챗봇) / `HUMAN`(고객) / `SYSTEM`(시스템 안내)",
    )
    message_text: str = Field(description="메시지 본문.")
    sent_at: datetime = Field(description="메시지가 기록된 시각(UTC).")


class ChatVerifyRequest(BaseModel):
    """출생연도 4자리 본인 확인 입력."""

    birth_year: str = Field(
        pattern=r"^\d{4}$",
        description="고객 출생연도 4자리. 거래 고객의 생년월일과 대조한다.",
        examples=["1958"],
    )


class ChatSessionDetailResponse(BaseModel):
    """고객 화면이 접속·재접속 시 받는 세션 상태와 대화 이력."""

    chat_session_id: str = Field(
        min_length=1,
        max_length=64,
        description="채팅 세션 id.",
    )
    transaction_id: int = Field(gt=0, description="이 상담이 다루는 거래 id.")
    status: ChatSessionStatusValue = Field(
        description=(
            "세션 상태. `URL_SENT`(접속 전) / `IN_PROGRESS`(상담 중) / "
            "`HANDOFF_REQUESTED`(상담사 연결 대기) / `DONE`(종료) / `FAILED`(실패)"
        ),
    )
    is_older: bool = Field(
        description="참이면 고령자 전용 UI 로 분기한다(화면 분기는 프론트가 한다).",
    )
    question_step: int = Field(
        ge=0,
        description="진행 중인 질문 번호. 0 이면 아직 첫 질문을 내보내지 않았다.",
    )
    conversation_phase: ChatConversationPhaseValue | None = Field(
        default=None,
        description="현재 고객 대화 단계. 본인 확인 전에는 `null`.",
    )
    input_mode: ChatInputMode = Field(
        description="현재 화면에서 허용할 고객 입력 방식.",
    )
    question_id: DiscriminationQuestionId | None = Field(
        default=None,
        description="현재 퀵리플라이 질문. 자유 대화 또는 종료 상태이면 `null`.",
    )
    quick_replies: list[ChatQuickReplyResponse] = Field(
        default_factory=list,
        description="현재 표시할 퀵리플라이. 재접속 시에도 복원한다.",
    )
    confirmed_fraud_type: FraudTypeCode | None = Field(
        default=None,
        description="네/아니요 응답으로 확정된 의심 사기유형.",
    )
    messages: list[ChatMessageResponse] = Field(
        description="이 세션의 전체 대화 이력(오래된 순).",
    )


class ChatTurnResponse(BaseModel):
    """버튼 선택·고객 답변 한 턴의 결과."""

    chat_session_id: str = Field(
        min_length=1,
        max_length=64,
        description="채팅 세션 id.",
    )
    status: ChatSessionStatusValue = Field(
        description="이 턴을 처리한 뒤의 세션 상태.",
    )
    question_step: int = Field(
        ge=0,
        description=(
            "이 턴을 처리한 뒤의 질문 번호. 재질문 턴에서는 값이 그대로 유지된다."
        ),
    )
    conversation_phase: ChatConversationPhaseValue | None = None
    input_mode: ChatInputMode
    question_id: DiscriminationQuestionId | None = None
    quick_replies: list[ChatQuickReplyResponse] = Field(default_factory=list)
    confirmed_fraud_type: FraudTypeCode | None = None
    # 이번 턴에 챗봇이 보낸 메시지 본문. 대화 이력은 이미 chat_messages 에 저장돼 있다.
    messages: list[str] = Field(
        description=(
            "이번 턴에 챗봇이 보낸 메시지 본문만 담는다(누적 이력이 아니다). "
            "전체 이력은 `GET /chat/{chat_session_id}` 로 받는다."
        ),
    )


class TransactionChatSessionStatusResponse(BaseModel):
    """담당자 거래 목록에 표시할 채팅 세션 상태."""

    transaction_id: int = Field(gt=0, description="조회한 거래 id.")
    chat_session_id: str | None = Field(
        default=None,
        description="연결된 채팅 세션 id. 세션이 없으면 `null`.",
    )
    status: ChatSessionStatusValue | None = Field(
        default=None,
        description="세션의 현재 상태. 세션이 없으면 `null`.",
    )


class TransactionChatSessionDetailResponse(BaseModel):
    """담당자가 거래 한 건의 상담 내용을 열었을 때 받는 내역"""

    transaction_id: int = Field(gt=0, description="조회한 거래 id.")
    chat_session_id: str | None = Field(
        default=None,
        description="연결된 채팅 세션 id. 세션이 없으면 `null`.",
    )
    status: ChatSessionStatusValue | None = Field(
        default=None,
        description="세션의 현재 상태. 세션이 없으면 `null`.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="상담이 끝난 시각. 진행 중이거나 세션이 없으면 `null`.",
    )
    messages: list[ChatMessageResponse] = Field(
        default_factory=list,
        description="이 세션의 전체 대화 이력(오래된 순).",
    )
    confirmed_fraud_type: FraudTypeCode | None = Field(
        default=None,
        description="네/아니요 판별에서 확정된 의심 사기유형.",
    )


@dataclass(frozen=True, slots=True)
class RetrievedChatbotGuideChunkDTO:
    """챗봇 고객 대응 가이드 검색 결과 청크."""

    content: str
    source_title: str
    page: int | None
    distance: float


__all__ = [
    "AnswerAnalysisResult",
    "AnswerQualityVerdict",
    "ChatConversationPhaseValue",
    "ChatDiscriminationAction",
    "ChatDiscriminationActionRequest",
    "ChatInputMode",
    "ChatMessageResponse",
    "ChatQuickReplyResponse",
    "ChatSessionDetailResponse",
    "ChatSessionStatusValue",
    "ChatTurnResponse",
    "ChatUiEvent",
    "ChatVerifyRequest",
    "CreateChatRequest",
    "DiscriminationQuestionId",
    "ExtractedGuideSearchQuery",
    "FraudTypeCode",
    "RetrievedChatbotGuideChunkDTO",
    "SendChatMessageRequest",
    "TransactionChatSessionDetailResponse",
    "TransactionChatSessionStatusResponse",
]
