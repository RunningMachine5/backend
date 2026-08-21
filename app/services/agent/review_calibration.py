"""담당자 검토 결과로 Agent 분류·유사도 설정 후보를 비교한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.agent_status import ClassificationStatus
from app.services.agent.case_similarity import (
    CaseSimilarityConfig,
    CaseSimilarityFeatures,
    rank_similar_cases,
)
from app.services.agent.type_confidence import (
    TypeConfidenceThresholds,
    calculate_type_confidence,
)


@dataclass(frozen=True, slots=True)
class ReviewedCase:
    """담당자가 사기로 확정한 사건의 평가용 최소 정보다."""

    case_id: str
    type_scores: dict[str, float]
    matched_components: dict[str, list[str]]
    risk_score: int
    risk_grade: str
    decision: str
    confirmed_fraud_type: str | None


@dataclass(frozen=True, slots=True)
class CalibrationCandidate:
    """코드 변경 없이 비교할 유형 확실성·유사도 후보 설정이다."""

    name: str
    minimum_top_score: float
    minimum_margin: float
    minimum_similarity: float


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """후보 설정 하나의 담당자 검토 결과 비교 지표다."""

    candidate: CalibrationCandidate
    reviewed_case_count: int
    confirmed_case_count: int
    ambiguous_rate: float | None
    confident_type_agreement_rate: float | None
    unsafe_confident_count: int
    unnecessary_investigation_rate: float | None
    similarity_coverage_rate: float | None
    similarity_precision_at_1: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": {
                "name": self.candidate.name,
                "minimum_top_score": self.candidate.minimum_top_score,
                "minimum_margin": self.candidate.minimum_margin,
                "minimum_similarity": self.candidate.minimum_similarity,
            },
            "reviewed_case_count": self.reviewed_case_count,
            "confirmed_case_count": self.confirmed_case_count,
            "ambiguous_rate": self.ambiguous_rate,
            "confident_type_agreement_rate": self.confident_type_agreement_rate,
            "unsafe_confident_count": self.unsafe_confident_count,
            "unnecessary_investigation_rate": self.unnecessary_investigation_rate,
            "similarity_coverage_rate": self.similarity_coverage_rate,
            "similarity_precision_at_1": self.similarity_precision_at_1,
        }

    def to_csv_row(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.name,
            "minimum_top_score": self.candidate.minimum_top_score,
            "minimum_margin": self.candidate.minimum_margin,
            "minimum_similarity": self.candidate.minimum_similarity,
            "reviewed_case_count": self.reviewed_case_count,
            "confirmed_case_count": self.confirmed_case_count,
            "ambiguous_rate": self.ambiguous_rate,
            "confident_type_agreement_rate": self.confident_type_agreement_rate,
            "unsafe_confident_count": self.unsafe_confident_count,
            "unnecessary_investigation_rate": self.unnecessary_investigation_rate,
            "similarity_coverage_rate": self.similarity_coverage_rate,
            "similarity_precision_at_1": self.similarity_precision_at_1,
        }


DEFAULT_CALIBRATION_CANDIDATES = (
    CalibrationCandidate("sensitive-sim-055", 0.50, 0.10, 0.55),
    CalibrationCandidate("sensitive-sim-060", 0.50, 0.10, 0.60),
    CalibrationCandidate("sensitive-sim-065", 0.50, 0.10, 0.65),
    CalibrationCandidate("baseline-sim-055", 0.60, 0.15, 0.55),
    CalibrationCandidate("baseline-sim-060", 0.60, 0.15, 0.60),
    CalibrationCandidate("baseline-sim-065", 0.60, 0.15, 0.65),
    CalibrationCandidate("strict-sim-055", 0.70, 0.20, 0.55),
    CalibrationCandidate("strict-sim-060", 0.70, 0.20, 0.60),
    CalibrationCandidate("strict-sim-065", 0.70, 0.20, 0.65),
)


def evaluate_review_calibration(
    reviewed_cases: list[ReviewedCase],
    *,
    candidates: tuple[CalibrationCandidate, ...] = DEFAULT_CALIBRATION_CANDIDATES,
) -> list[CalibrationResult]:
    """동일한 담당자 확정 사건으로 설정 후보별 지표를 계산한다."""

    confirmed_cases = [
        case
        for case in reviewed_cases
        if case.decision == "CONFIRMED_FRAUD" and case.confirmed_fraud_type
    ]
    return [
        _evaluate_candidate(
            reviewed_case_count=len(reviewed_cases),
            confirmed_cases=confirmed_cases,
            candidate=candidate,
        )
        for candidate in candidates
    ]


def _evaluate_candidate(
    *,
    reviewed_case_count: int,
    confirmed_cases: list[ReviewedCase],
    candidate: CalibrationCandidate,
) -> CalibrationResult:
    thresholds = TypeConfidenceThresholds(
        minimum_top_score=candidate.minimum_top_score,
        minimum_margin=candidate.minimum_margin,
    )
    confidence_rows = [
        (case, calculate_type_confidence(case.type_scores, thresholds=thresholds))
        for case in confirmed_cases
    ]
    confident_rows = [
        row
        for row in confidence_rows
        if row[1].classification_status == ClassificationStatus.CONFIDENT
    ]
    top_type_matches = [
        row for row in confidence_rows if row[1].top_type_code == row[0].confirmed_fraud_type
    ]
    unnecessary_rows = [
        row
        for row in top_type_matches
        if row[1].classification_status == ClassificationStatus.AMBIGUOUS
    ]
    matching_confident_rows = [
        row for row in confident_rows if row[1].top_type_code == row[0].confirmed_fraud_type
    ]
    unsafe_confident_count = len(confident_rows) - len(matching_confident_rows)

    coverage_count, precision_count = _evaluate_similarity(
        confirmed_cases,
        minimum_similarity=candidate.minimum_similarity,
    )
    confirmed_count = len(confirmed_cases)
    return CalibrationResult(
        candidate=candidate,
        reviewed_case_count=reviewed_case_count,
        confirmed_case_count=confirmed_count,
        ambiguous_rate=_rate(
            len(confidence_rows) - len(confident_rows), confirmed_count
        ),
        confident_type_agreement_rate=_rate(
            len(matching_confident_rows), len(confident_rows)
        ),
        unsafe_confident_count=unsafe_confident_count,
        unnecessary_investigation_rate=_rate(
            len(unnecessary_rows), len(top_type_matches)
        ),
        similarity_coverage_rate=_rate(coverage_count, confirmed_count),
        similarity_precision_at_1=_rate(precision_count, coverage_count),
    )


def _evaluate_similarity(
    confirmed_cases: list[ReviewedCase],
    *,
    minimum_similarity: float,
) -> tuple[int, int]:
    features = {case.case_id: _to_similarity_features(case) for case in confirmed_cases}
    confirmed_type_by_case_id = {
        case.case_id: case.confirmed_fraud_type for case in confirmed_cases
    }
    config = CaseSimilarityConfig(minimum_similarity=minimum_similarity)
    coverage_count = 0
    precision_count = 0
    for case in confirmed_cases:
        ranked = rank_similar_cases(
            features[case.case_id],
            list(features.values()),
            top_k=1,
            config=config,
        )
        if not ranked:
            continue
        coverage_count += 1
        if confirmed_type_by_case_id[ranked[0].case_id] == case.confirmed_fraud_type:
            precision_count += 1
    return coverage_count, precision_count


def _to_similarity_features(case: ReviewedCase) -> CaseSimilarityFeatures:
    return CaseSimilarityFeatures(
        case_id=case.case_id,
        type_scores=case.type_scores,
        matched_components=case.matched_components,
        risk_score=case.risk_score,
        risk_grade=case.risk_grade,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


__all__ = [
    "CalibrationCandidate",
    "CalibrationResult",
    "DEFAULT_CALIBRATION_CANDIDATES",
    "ReviewedCase",
    "evaluate_review_calibration",
]
