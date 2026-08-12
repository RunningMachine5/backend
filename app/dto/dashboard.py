# 프론트에 반환할 통합 응답 DTO
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")

# 각 섹션의 상태를 나타내는 Enum
class SectionStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    PROCESSING = "PROCESSING"
    EMPTY = "EMPTY"
    FAILED = "FAILED"
    NOT_AVAILABLE = "NOT_AVAILABLE"

# 각 섹션의 상태와 데이터를 담는 DTO
class SectionResult(BaseModel, Generic[T]):
    status: SectionStatus
    data: T | None = None
    error_message: str | None = None

# 각 섹션의 데이터 구조를 정의하는 DTO
class TransactionView(BaseModel):
    transaction_id: str
    transaction_datetime: str # 거래 시간
    transaction_amount: int # 거래 금액
    channel: str # 거래 방법(atm, 카드 등)
    location: str # 거래 위치
    customer_id: str # 고객 ID
    source_account_id: str # 출금 계좌 ID
    recipient_account_id: str | None = None # 수취 계좌 ID

# Fraud detection model 결과를 담는 DTO
class MLView(BaseModel):
    prediction_status: str # 예측 상태
    is_fraud: bool | None = None # 사기 여부
    fraud_probability: float | None = Field( # 사기 확률
        default=None,
        ge=0,
        le=1,
    )
    shap: dict[str, float] = Field(default_factory=dict)
    model_name: str | None = None
    model_version: str | None = None

# 유사 사례 결과를 담는 DTO
class SimilarCaseView(BaseModel):
    similar_case_id: str
    similarity_rank: int = Field(ge=1, le=3) # 이런 값도 있나?
    similarity_score: float = Field(ge=0, le=1)
    similarity_reason: str

# 덕현님 에이전트가 반환하는 결과를 담는 DTO
class CaseAgentView(BaseModel):
    execution_status: str # 실행 상태
    failure_reason: str | None = None # 뭐에 실패한 거임?
    risk_score: int | None = Field(default=None, ge=0, le=100) # 위험 점수
    risk_grade: str | None = None # 위험 등급

    # 다른 Agent의 상세 구조가 변경될 수 있어 일단 dict로 받는다.
    rule_result: dict[str, Any] | None = None
    investigation_result: dict[str, Any] | None = None
    similar_case_results: list[SimilarCaseView] = Field(
        default_factory=list
    )
    response_result: dict[str, Any] | None = None
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    created_at: str | None = None
    completed_at: str | None = None


class ChatMessageView(BaseModel):
    message_id: str
    sender_type: str # 왜 이런 식으로 저장하지?
    message_text: str
    sent_at: str


class ChatView(BaseModel):
    chat_session_id: str | None = None
    session_status: str | None = None # 이게 뭐임
    started_at: str | None = None
    closed_at: str | None = None
    messages: list[ChatMessageView] = Field(default_factory=list)

# 선택한 사건 하나의 상세 화면 전체 데이터
class CaseDetailResponse(BaseModel):
    case_id: str
    transaction_id: str

    transaction: SectionResult[TransactionView]
    ml: SectionResult[MLView]
    case_agent: SectionResult[CaseAgentView]
    chat: SectionResult[ChatView]
    review: SectionResult[dict[str, Any]]

# 유사 사례 뜻하는 거임?
class CaseListItemResponse(BaseModel):
    case_id: str
    transaction_id: str
    execution_status: str
    risk_score: int | None = None
    risk_grade: str | None = None
    primary_fraud_type: str | None = None
    transaction_amount: int
    transaction_datetime: str
    review_status: str

# 대시보드 요약 정보를 담는 DTO. 이걸 왜 담음?
class DashboardSummaryResponse(BaseModel):
    pending_case_count: int = Field(ge=0)
    very_high_case_count: int = Field(ge=0)
    suspicious_amount: int = Field(ge=0)
    completed_case_count: int = Field(ge=0)
    email_required_count: int = Field(ge=0)
    prevented_amount: int = Field(ge=0)