"""AI Agent 사건 실행과 대시보드 응답에 사용하는 DTO."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InformationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.domain.enums import RiskGrade


@dataclass(frozen=True, slots=True)
class AgentInputDTO:
    """탐지와 위험등급 산정이 끝난 후 Agent에 전달하는 입력 DTO."""

    transaction_id: int
    fraud_type_score_result_id: int
    risk_score: int
    risk_grade: RiskGrade


@dataclass(frozen=True, slots=True)
class RuleEvidenceDTO:
    """Rule Engine에서 실제로 적중한 구성요소 근거."""

    fraud_type: str
    evidence_code: str
    observed_value: Any
    contribution: float


@dataclass(frozen=True, slots=True)
class FraudTypeScoreResultDTO:
    """Agent가 조회하여 사용하는 Rule Engine 유형 점수 결과."""

    fraud_type_score_result_id: int
    rule_filter_status: RuleFilterStatus
    primary_fraud_type: str | None
    type_scores: dict[str, float]
    matched_components: list[RuleEvidenceDTO]


@dataclass(frozen=True, slots=True)
class RecommendedActionDTO:
    """사건별 권장 대응 조치."""

    priority: int
    action_code: str
    action: str
    reason: str
    required: bool
    procedure_steps: list[str]
    cautions: list[str]


@dataclass(frozen=True, slots=True)
class ChecklistItemDTO:
    """모니터링 담당자가 확인할 체크리스트 항목."""

    item_code: str
    label: str
    required: bool


@dataclass(frozen=True, slots=True)
class ResponsePlanDTO:
    """내부 정책과 RAG 문서를 이용해 생성한 사건별 대응 계획."""

    applied_fraud_type: str
    information_status: InformationStatus
    summary: str
    recommended_actions: list[RecommendedActionDTO]
    checklist: list[ChecklistItemDTO]


@dataclass(frozen=True, slots=True)
class CustomerResponseContextDTO:
    """가이드 생성 직전에 조회한 고객 챗봇 응답과 재채점 결과."""

    customer_answers: list[str]
    type_scores: dict[str, float]

    @property
    def has_customer_response(self) -> bool:
        return bool(self.customer_answers or self.type_scores)


@dataclass(frozen=True, slots=True)
class SimilarCaseResultDTO:
    """대시보드에 표시하는 유사 완료 사건 한 건."""

    similar_case_id: str
    similarity_rank: int
    similarity_score: float
    similarity_reason: str


@dataclass(frozen=True, slots=True)
class InvestigationResultDTO:
    """유형 확실성 판단과 애매한 유형의 조사 결과."""

    classification_status: ClassificationStatus
    score_margin: float
    investigation_status: InvestigationStatus
    recommended_fraud_type: str | None
    recommendation_reason: str | None
    best_similarity_score: float | None
    common_evidence_codes: list[str]
    confirmed_case_count: int


@dataclass(frozen=True, slots=True)
class AgentResponseDTO:
    """Agent 사건 상세조회와 대시보드 전달에 사용하는 최종 DTO."""

    case_id: str
    transaction_id: int
    execution_status: AgentExecutionStatus
    failure_reason: str | None
    rule_result: FraudTypeScoreResultDTO
    risk_score: int
    risk_grade: RiskGrade
    investigation_result: InvestigationResultDTO | None
    best_similar_case_id: str | None
    similar_case_results: list[SimilarCaseResultDTO]
    response_result: ResponsePlanDTO | None
    generation_metadata: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class FraudAlertEmailCommand:
    """유형 판단 후 고객 이메일 서비스에 전달하는 자동 발송 명령."""

    transaction_id: int
    primary_suspected_type: str
    secondary_suspected_type: str
    classification_status: ClassificationStatus


def dto_to_dict(value: Any) -> Any:
    """중첩 DTO를 JSON 직렬화가 가능한 기본 타입으로 변환한다."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: dto_to_dict(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {key: dto_to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dto_to_dict(item) for item in value]
    return value


__all__ = [
    "AgentExecutionStatus",
    "AgentInputDTO",
    "AgentResponseDTO",
    "ChecklistItemDTO",
    "ClassificationStatus",
    "CustomerResponseContextDTO",
    "FraudAlertEmailCommand",
    "FraudTypeScoreResultDTO",
    "InformationStatus",
    "InvestigationResultDTO",
    "InvestigationStatus",
    "RecommendedActionDTO",
    "ResponsePlanDTO",
    "RiskGrade",
    "RuleFilterStatus",
    "RuleEvidenceDTO",
    "SimilarCaseResultDTO",
    "dto_to_dict",
]
