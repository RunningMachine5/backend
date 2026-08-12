"""애매한 사기 유형을 과거 완료 사건과 비교할 때 사용하는 DTO."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SimilarResolvedCaseDTO:
    """유사도 계산을 통과한 과거 완료 사건 한 건."""

    case_id: str
    transaction_id: str
    confirmed_fraud_type: str
    similarity_score: float
    common_evidence_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedCaseDetailDTO:
    """조사 Agent가 선택한 과거 사건의 실제 검토·처리 결과."""

    case_id: str
    transaction_id: str
    confirmed_fraud_type: str
    performed_actions: list[dict[str, Any]]
    checklist_results: list[dict[str, Any]]
    resolution_summary: str | None
    response_result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class TransactionContextDTO:
    """유형 비교에 추가 거래 문맥이 필요할 때 조회하는 최소 거래정보."""

    transaction_id: str
    transaction_amount: int
    channel: str
    transaction_datetime: datetime
    location: str


__all__ = [
    "ResolvedCaseDetailDTO",
    "SimilarResolvedCaseDTO",
    "TransactionContextDTO",
]
