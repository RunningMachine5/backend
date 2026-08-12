"""애매한 사기 유형을 과거 완료 사건과 비교할 때 사용하는 DTO."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SimilarResolvedCaseDTO:
    """유사도 계산을 통과한 과거 완료 사건 한 건."""

    case_id: str
    similarity_score: float
    common_evidence_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedCaseDetailDTO:
    """조사 Agent가 선택한 과거 사건의 실제 검토·처리 결과."""

    case_id: str
    confirmed_fraud_type: str


__all__ = [
    "ResolvedCaseDetailDTO",
    "SimilarResolvedCaseDTO",
]
