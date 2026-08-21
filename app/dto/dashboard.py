# 프론트에 반환할 통합 응답 DTO

from datetime import datetime
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
    transaction_id: int
    transaction_datetime: datetime # 거래 시간
    transaction_amount: int # 거래 금액
    channel: str # 거래 방법(atm, 카드 등)
    location_lat: float | None = None
    location_lon: float | None = None
    customer_id: int | None = None # 고객 ID
    source_account_number: str # 출금 계좌 번호
    recipient_account_number: str # 수취 계좌 번호
    access_medium: str | None = None
    operating_system: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    num_connection_failure: int = 0
    rooting_jailbreak_indicator: bool = False
    mobile_roaming_indicator: bool = False
    vpn_indicator: bool = False
    terminal_malicious_behavior_detected: bool = False

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
    similarity_rank: int = Field(ge=1, le=3) # 유사한 사례 상위 3개 보여준다는 뜻
    similarity_score: float = Field(ge=0, le=1)
    similarity_reason: str

# 덕현님 에이전트가 반환하는 결과를 담는 DTO
class CaseAgentView(BaseModel):
    execution_status: str # 실행 상태
    failure_reason: str | None = None # 에이전트 실행 실패 이유
    risk_score: int | None = Field(default=None, ge=0, le=100) # 위험 점수
    risk_grade: str | None = None # 위험 등급
    best_similar_case_id: str | None = None

    # 다른 Agent의 상세 구조가 변경될 수 있어 일단 dict로 받는다.
    rule_result: dict[str, Any] | None = None
    investigation_result: dict[str, Any] | None = None
    similar_case_results: list[SimilarCaseView] = Field(
        default_factory=list
    )
    response_result: dict[str, Any] | None = None

# 채팅 부분(이거 거의 그대로 감)
class ChatMessageView(BaseModel):
    message_id: int
    sender_type: str # 채팅 메시지를 보낸 주체
    message_text: str
    sent_at: datetime
class ChatView(BaseModel):
    chat_session_id: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    messages: list[ChatMessageView] = Field(default_factory=list)

# 선택한 사건 하나의 상세 화면 전체 데이터
class CaseDetailResponse(BaseModel):
    case_id: str
    transaction_id: int

    transaction: SectionResult[TransactionView]
    ml: SectionResult[MLView]
    case_agent: SectionResult[CaseAgentView]
    chat: SectionResult[ChatView]
    review: SectionResult[dict[str, Any]]

# 위험 점수와 위험 등급, 사기 유형 등의 거래 정보
class CaseListItemResponse(BaseModel):
    case_id: str
    transaction_id: int
    execution_status: str
    risk_score: int | None = None
    risk_grade: str | None = None
    primary_fraud_type: str | None = None
    transaction_amount: int
    transaction_datetime: datetime
    received_at: datetime
    ip_address: str | None = None
    review_status: str

# 처리 페이지 목록 전체 응답
class CaseListResponse(BaseModel):
    items: list[CaseListItemResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_count: int = Field(ge=0)

# 대시보드 요약 정보를 담는 DTO. 이걸 왜 담음?
class DashboardSummaryResponse(BaseModel):
    pending_case_count: int = Field(ge=0)
    very_high_case_count: int = Field(ge=0)
    suspicious_amount: int = Field(ge=0)
    completed_case_count: int = Field(ge=0)
    email_required_count: int = Field(ge=0)
    prevented_amount: int = Field(ge=0)

# 대시보드 기간 설정 값
class DashboardOverviewPeriod(BaseModel):
    period_start: datetime
    period_end: datetime

# 대시보드 최상단 카드 값
class DashboardOverviewSummary(BaseModel):
    total_transaction_count: int # 총 거래 건수
    suspicious_transaction_count: int # 사기 거래 건수
    priority_review_count: int # 우선 대응 필요한 건수
    suspicious_amount: int # 사기 의심 사건 총 피해 금액
    rule_analysis_completed_count: int # 룰 분석 완료 건수

# 우선순위 검토 대상 그래프
class PriorityTrendPoint(BaseModel):
    date: str
    very_high_count: int
    high_count: int
    total_count: int

# 의심 거래 건수/액수 그래프
class SuspiciousTrendPoint(BaseModel):
    date: str
    suspicious_count: int
    suspicious_amount: int

# 위험등급별 건수/액수 그래프
class DistributionItem(BaseModel):
    label: str
    count: int
    amount: int

# 대시보드 에이전트 분석 그래프
class DashboardAgentInsight(BaseModel):
    insight_id: str
    title: str
    summary: str | None = None
    chart_spec: dict[str, Any] | None = None
    created_at: datetime


# 대시보드 그래프 
class DashboardOverviewResponse(BaseModel):
    period: DashboardOverviewPeriod
    summary: DashboardOverviewSummary
    priority_trend: list[PriorityTrendPoint]
    suspicious_trend: list[SuspiciousTrendPoint]
    risk_grade_distribution: list[DistributionItem]
    channel_distribution: list[DistributionItem]
    agent_insight: DashboardAgentInsight | None = None
