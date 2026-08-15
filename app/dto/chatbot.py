"""고객 대응 챗봇의 API 및 LLM 구조화 출력 계약."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.customer_action_codes import FINAL_CUSTOMER_ACTION_CODES
from app.domain.fraud_circumstance_codes import FINAL_FRAUD_CIRCUMSTANCE_CODES
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


CustomerActionCode = Literal[*FINAL_CUSTOMER_ACTION_CODES]
FraudCircumstanceCode = Literal[*FINAL_FRAUD_CIRCUMSTANCE_CODES]
FraudTypeCode = Literal[*FINAL_FRAUD_TYPE_CODES]

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758132371&cot=14
class CreateChatRequest(BaseModel):
    """거래에 연결된 고객 채팅 세션 생성 요청."""

    transaction_id: str = Field(min_length=1, max_length=64)
    # 룰 채점 점수 내림차순 상위 2개 사기유형. 유형판별 질문(PRD 2.4) 선택에 쓴다.
    # 룰 채점 실패로 유형 점수가 없으면 생략하며, 그 세션은 일반 질문 폴백을 쓴다.
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

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758183440&cot=14
class CreateChatResponse(BaseModel):
    """생성되었거나 기존에 존재하던 고객 채팅 세션."""

    chat_session_id: str = Field(min_length=1, max_length=64)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758232165&cot=14
class AnswerQualityVerdict(StrEnum):
    """고객 답변 충실도 평가 결과."""

    SUFFICIENT = "SUFFICIENT"
    TOO_VAGUE = "TOO_VAGUE"
    NON_ANSWER = "NON_ANSWER"
    REFUSAL = "REFUSAL"
    WANT_END = "WANT_END"

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758183824&cot=14
class AnswerEvaluationResult(BaseModel):
    """평가 LLM이 반환할 JSON 객체 스키마.

    ``AnswerQualityVerdict``는 ``verdict`` 한 필드의 허용 문자열 집합이고,
    이 DTO는 ``{"verdict": "SUFFICIENT"}`` 형태의 전체 출력을 검증한다.
    """

    verdict: AnswerQualityVerdict

class ExtractedCustomerAction(BaseModel):
    """고객 답변에서 추출한 화이트리스트 행동과 원문 근거."""

    type: CustomerActionCode
    evidence: str = Field(min_length=1)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758280649&cot=14
class CustomerActionExtractionResult(BaseModel):
    """고객 행동 추출 LLM의 구조화 출력."""

    customer_actions: list[ExtractedCustomerAction]

class ExtractedFraudCircumstance(BaseModel):
    """고객 답변에서 추출한 화이트리스트 사기 정황과 원문 근거."""

    type: FraudCircumstanceCode
    evidence: str = Field(min_length=1)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758280824&cot=14
class FraudCircumstanceExtractionResult(BaseModel):
    """사기 정황 추출 LLM의 구조화 출력."""

    fraud_circumstances: list[ExtractedFraudCircumstance]


class GeneratedActionGuide(BaseModel):
    """대응 가이드 생성 LLM이 액션 하나에 대해 만든 안내."""

    type: CustomerActionCode
    # 근거만으로 안내를 쓸 수 없으면 빈 문자열이며, 호출부가 B.5 문구로 대체한다.
    guidance: str


# 프롬프트 A.4의 출력 형식
class GuideResponseGenerationResult(BaseModel):
    """대응 가이드 생성 LLM의 구조화 출력."""

    guides: list[GeneratedActionGuide]


class ChatButtonAction(StrEnum):
    """최초 알림 뒤 고객이 선택할 수 있는 버튼 액션."""

    START_CHAT = "START_CHAT"
    REQUEST_HANDOFF = "REQUEST_HANDOFF"
    END_CHAT = "END_CHAT"

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326094&cot=14
class ChatButtonActionRequest(BaseModel):
    action: ChatButtonAction

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326541&cot=14
class SendChatMessageRequest(BaseModel):
    message_text: str = Field(min_length=1)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326686&cot=14
class ChatMessageResponse(BaseModel):
    message_id: int = Field(gt=0)
    sender_type: Literal["AI", "HUMAN", "SYSTEM"]
    message_text: str
    sent_at: datetime


class ChatSessionStatusChangedEventPayload(BaseModel):
    """담당자 화면에 전달하는 채팅 세션 상태 변경 SSE 이벤트."""

    transaction_id: int = Field(gt=0)
    chat_session_id: str = Field(min_length=1, max_length=64)
    status: Literal[
        "URL_SENT",
        "IN_PROGRESS",
        "HANDOFF_REQUESTED",
        "DONE",
        "FAILED",
    ]


@dataclass(frozen=True, slots=True)
class RetrievedChatbotGuideChunkDTO:
    """챗봇 고객 대응 가이드 검색 결과 청크."""

    content: str
    source_title: str
    page: int | None
    distance: float


__all__ = [
    "AnswerEvaluationResult",
    "AnswerQualityVerdict",
    "ChatButtonAction",
    "ChatButtonActionRequest",
    "ChatMessageResponse",
    "ChatSessionStatusChangedEventPayload",
    "CreateChatRequest",
    "CreateChatResponse",
    "CustomerActionCode",
    "CustomerActionExtractionResult",
    "ExtractedCustomerAction",
    "ExtractedFraudCircumstance",
    "FraudCircumstanceCode",
    "FraudCircumstanceExtractionResult",
    "FraudTypeCode",
    "GeneratedActionGuide",
    "GuideResponseGenerationResult",
    "RetrievedChatbotGuideChunkDTO",
    "SendChatMessageRequest",
]
