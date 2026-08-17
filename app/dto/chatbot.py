"""고객 대응 챗봇의 API 및 LLM 구조화 출력 계약."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.fraud_circumstance_codes import FINAL_FRAUD_CIRCUMSTANCE_CODES
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


FraudCircumstanceCode = Literal[*FINAL_FRAUD_CIRCUMSTANCE_CODES]
FraudTypeCode = Literal[*FINAL_FRAUD_TYPE_CODES]
# ChatSessionStatus 5종(스키마 3.3). API 응답과 SSE 페이로드가 같은 집합을 쓴다.
ChatSessionStatusValue = Literal[
    "URL_SENT",
    "IN_PROGRESS",
    "HANDOFF_REQUESTED",
    "DONE",
    "FAILED",
]

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758132371&cot=14
class CreateChatRequest(BaseModel):
    """거래에 연결된 고객 채팅 세션 생성 입력(PRD 2.1).

    세션 생성은 HTTP 로 열지 않으므로 요청 본문이 아니다. FDS 파이프라인과
    로컬 테스트 스크립트가 ``ChatSessionCreator.create`` 에 넘길 값을 여기서 검증한다.
    """

    # transactions.id 와 chat_sessions.transaction_id 는 DB가 발급하는 BIGINT 다.
    transaction_id: int = Field(gt=0)
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

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758232165&cot=14
class AnswerQualityVerdict(StrEnum):
    """고객 답변 충실도 평가 결과."""

    SUFFICIENT = "SUFFICIENT"
    TOO_VAGUE = "TOO_VAGUE"
    WANT_END = "WANT_END"

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758183824&cot=14
class AnswerEvaluationResult(BaseModel):
    """평가 LLM이 반환할 JSON 객체 스키마.

    ``AnswerQualityVerdict``는 ``verdict`` 한 필드의 허용 문자열 집합이고,
    이 DTO는 ``{"verdict": "SUFFICIENT"}`` 형태의 전체 출력을 검증한다.
    """

    verdict: AnswerQualityVerdict

class ExtractedGuideSearchQuery(BaseModel):
    """고객 답변에서 분해한 독립 검색 단위와 원문 근거."""

    title: str = Field(min_length=1, max_length=120)
    search_query: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758280649&cot=14
class GuideSearchQueryExtractionResult(BaseModel):
    """대응 가이드 검색 질의 분해 LLM의 구조화 출력."""

    guide_search_queries: list[ExtractedGuideSearchQuery] = Field(max_length=5)

class ExtractedFraudCircumstance(BaseModel):
    """고객 답변에서 추출한 화이트리스트 사기 정황과 원문 근거."""

    type: FraudCircumstanceCode
    evidence: str = Field(min_length=1)

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758280824&cot=14
class FraudCircumstanceExtractionResult(BaseModel):
    """사기 정황 추출 LLM의 구조화 출력."""

    fraud_circumstances: list[ExtractedFraudCircumstance]


class GeneratedSearchQueryGuide(BaseModel):
    """대응 가이드 생성 LLM이 검색 단위 하나에 대해 만든 안내."""

    position: int = Field(ge=1, le=5)
    # 근거만으로 안내를 쓸 수 없으면 빈 문자열이며, 호출부가 B.5 문구로 대체한다.
    guidance: str


# 프롬프트 A.4의 출력 형식
class GuideResponseGenerationResult(BaseModel):
    """대응 가이드 생성 LLM의 구조화 출력."""

    guides: list[GeneratedSearchQueryGuide] = Field(max_length=5)


class ChatButtonAction(StrEnum):
    """최초 알림 뒤 고객이 선택할 수 있는 버튼 액션."""

    START_CHAT = "START_CHAT"
    REQUEST_HANDOFF = "REQUEST_HANDOFF"
    END_CHAT = "END_CHAT"

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326094&cot=14
class ChatButtonActionRequest(BaseModel):
    """최초 알림 뒤 고객이 누른 버튼."""

    action: ChatButtonAction = Field(
        description=(
            "`START_CHAT`(챗봇 상담 시작) / `REQUEST_HANDOFF`(상담사 연결) / "
            "`END_CHAT`(상담 종료)"
        ),
    )

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326541&cot=14
class SendChatMessageRequest(BaseModel):
    """고객이 질문에 답한 메시지 한 건."""

    message_text: str = Field(
        min_length=1,
        description="고객이 입력한 답변 원문. 빈 문자열은 받지 않는다.",
    )

# https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680758326686&cot=14
class ChatMessageResponse(BaseModel):
    """대화 이력에 쌓인 메시지 한 건."""

    message_id: int = Field(gt=0, description="메시지 id. 이력 정렬 기준이다.")
    sender_type: Literal["AI", "HUMAN", "SYSTEM"] = Field(
        description="작성 주체. `AI`(챗봇) / `HUMAN`(고객) / `SYSTEM`(시스템 안내)",
    )
    message_text: str = Field(description="메시지 본문.")
    sent_at: datetime = Field(description="메시지가 기록된 시각(UTC).")


class ChatVerifyRequest(BaseModel):
    """출생연도 4자리 간이 본인인증(PRD 2.2).

    실패 횟수 제한·URL 토큰·세션 TTL 은 MVP 범위 밖이다(PRD 3.3).
    """

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
    # 참이면 고령자 전용 UI 로 간다(PRD 2.2). 화면 분기는 프론트가 한다.
    is_older: bool = Field(
        description="참이면 고령자 전용 UI 로 분기한다(화면 분기는 프론트가 한다).",
    )
    question_step: int = Field(
        ge=0,
        description="진행 중인 질문 번호. 0 이면 아직 첫 질문을 내보내지 않았다.",
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
    # 이번 턴에 챗봇이 보낸 메시지 본문. 대화 이력은 이미 chat_messages 에 저장돼 있다.
    messages: list[str] = Field(
        description=(
            "이번 턴에 챗봇이 보낸 메시지 본문만 담는다(누적 이력이 아니다). "
            "전체 이력은 `GET /chat/{chat_session_id}` 로 받는다."
        ),
    )


class TransactionChatSessionStatusResponse(BaseModel):
    """담당자 거래 목록 항목 하나의 채팅 세션 상태(PRD 2.7).

    세션이 아직 없는 거래는 두 필드가 모두 ``null`` 이다.
    """

    transaction_id: int = Field(gt=0, description="조회한 거래 id.")
    chat_session_id: str | None = Field(
        default=None,
        description="연결된 채팅 세션 id. 세션이 없으면 `null`.",
    )
    status: ChatSessionStatusValue | None = Field(
        default=None,
        description="세션의 현재 상태. 세션이 없으면 `null`.",
    )


class ChatSessionStatusChangedEventPayload(BaseModel):
    """담당자 화면에 전달하는 채팅 세션 상태 변경 SSE 이벤트."""

    transaction_id: int = Field(
        gt=0,
        description="갱신할 거래 목록 항목의 거래 id.",
    )
    chat_session_id: str = Field(
        min_length=1,
        max_length=64,
        description="상태가 바뀐 채팅 세션 id.",
    )
    status: ChatSessionStatusValue = Field(description="변경된 뒤의 세션 상태.")


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
    "ChatSessionDetailResponse",
    "ChatSessionStatusChangedEventPayload",
    "ChatSessionStatusValue",
    "ChatTurnResponse",
    "ChatVerifyRequest",
    "CreateChatRequest",
    "ExtractedGuideSearchQuery",
    "ExtractedFraudCircumstance",
    "FraudCircumstanceCode",
    "FraudCircumstanceExtractionResult",
    "FraudTypeCode",
    "GeneratedSearchQueryGuide",
    "GuideResponseGenerationResult",
    "GuideSearchQueryExtractionResult",
    "RetrievedChatbotGuideChunkDTO",
    "SendChatMessageRequest",
    "TransactionChatSessionStatusResponse",
]
