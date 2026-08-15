"""애매한 사기 유형을 과거 완료 사건과 비교할 때 사용하는 DTO."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InvestigationAction(str, Enum):
    """LLM이 선택할 수 있는 제한된 조사 행동."""

    INSPECT_CASE = "INSPECT_CASE"
    STOP_RECOMMEND = "STOP_RECOMMEND"
    STOP_INSUFFICIENT = "STOP_INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class InvestigationActionDTO:
    """LLM의 다음 행동 선택 결과."""

    action: InvestigationAction
    case_id: str | None
    recommended_fraud_type: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class SimilarResolvedCaseDTO:
    """유사도 계산을 통과한 과거 완료 사건 한 건."""

    case_id: str
    confirmed_fraud_type: str
    similarity_score: float
    common_evidence_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedCaseDetailDTO:
    """조사 Agent가 선택한 과거 사건의 실제 검토·처리 결과."""

    case_id: str
    confirmed_fraud_type: str
    decision: str = ""
    performed_actions: list[dict[str, Any]] = field(default_factory=list)
    checklist_results: list[dict[str, Any]] = field(default_factory=list)
    resolution_summary: str | None = None


__all__ = [
    "InvestigationAction",
    "InvestigationActionDTO",
    "ResolvedCaseDetailDTO",
    "SimilarResolvedCaseDTO",
]
