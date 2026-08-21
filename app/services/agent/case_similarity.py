"""현재 사건과 대응 완료 사건 사이의 구조적 유사도를 계산한다."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from numbers import Real


@dataclass(frozen=True, slots=True)
class CaseSimilarityFeatures:
    """DB나 API DTO와 분리된 사건 유사도 계산용 최소 입력값이다."""

    case_id: str
    type_scores: Mapping[str, float]
    matched_components: Mapping[str, Sequence[str]]
    risk_score: float
    risk_grade: str


@dataclass(frozen=True, slots=True)
class CaseSimilarityWeights:
    """평가 결과에 따라 독립적으로 조정할 수 있는 유사도 가중치이다."""

    evidence: float = 0.50
    score_vector: float = 0.25
    risk_grade: float = 0.15
    risk_score: float = 0.10

    def __post_init__(self) -> None:
        values = {
            "evidence": self.evidence,
            "score_vector": self.score_vector,
            "risk_grade": self.risk_grade,
            "risk_score": self.risk_score,
        }
        validated = [
            _validate_non_negative_number(value, field_name=field_name)
            for field_name, value in values.items()
        ]
        if not math.isclose(math.fsum(validated), 1.0, abs_tol=1e-9):
            raise ValueError("유사도 가중치의 합은 1.0이어야 한다.")


@dataclass(frozen=True, slots=True)
class CaseSimilarityConfig:
    """후보 선택 기준과 점수 정규화 범위를 관리하는 설정이다."""

    weights: CaseSimilarityWeights = field(default_factory=CaseSimilarityWeights)
    # Seed 완료 사건 평가에서 Top-1 정확도를 유지하면서 검색 커버리지가 가장 높았던 값이다.
    minimum_similarity: float = 0.55
    maximum_risk_score: float = 100.0

    def __post_init__(self) -> None:
        _validate_unit_interval(
            self.minimum_similarity,
            field_name="minimum_similarity",
        )
        maximum = _validate_non_negative_number(
            self.maximum_risk_score,
            field_name="maximum_risk_score",
        )
        if maximum == 0.0:
            raise ValueError("maximum_risk_score는 0보다 커야 한다.")


@dataclass(frozen=True, slots=True)
class CaseSimilarityResult:
    """최종 점수와 이를 구성한 항목별 유사도를 함께 보존한다."""

    case_id: str
    similarity_score: float
    evidence_similarity: float
    score_vector_similarity: float
    risk_grade_similarity: float
    risk_score_similarity: float
    common_evidence_codes: tuple[str, ...]


def normalize_evidence_codes(
    matched_components: Mapping[str, Sequence[str]],
) -> frozenset[str]:
    """동일한 구성요소 이름의 유형 간 충돌을 막도록 근거 코드를 정규화한다."""

    if not isinstance(matched_components, Mapping):
        raise TypeError("matched_components는 유형 코드와 근거 목록의 매핑이어야 한다.")

    normalized: set[str] = set()
    for type_code, component_keys in matched_components.items():
        validated_type_code = _validate_non_empty_string(
            type_code,
            field_name="matched_components 유형 코드",
        )
        if isinstance(component_keys, (str, bytes)) or not isinstance(
            component_keys,
            Sequence,
        ):
            raise TypeError(
                f"matched_components[{validated_type_code}]는 근거 코드 목록이어야 한다."
            )

        for component_key in component_keys:
            validated_component_key = _validate_non_empty_string(
                component_key,
                field_name=f"matched_components[{validated_type_code}] 근거 코드",
            )
            normalized.add(f"{validated_type_code}:{validated_component_key}")

    return frozenset(normalized)


def calculate_evidence_similarity(
    current_evidence: frozenset[str],
    candidate_evidence: frozenset[str],
) -> float:
    """두 사건의 Rule 근거 집합을 Jaccard 방식으로 비교한다."""

    union = current_evidence | candidate_evidence
    # 양쪽 모두 근거가 없으면 유사하다는 증거도 없으므로 0점으로 처리한다.
    if not union:
        return 0.0
    return _round_similarity(len(current_evidence & candidate_evidence) / len(union))


def calculate_score_vector_similarity(
    current_scores: Mapping[str, float],
    candidate_scores: Mapping[str, float],
) -> float:
    """동일한 유형 코드 순서로 구성한 점수 벡터의 코사인 유사도를 계산한다."""

    current = _validate_type_scores(current_scores, field_name="current_scores")
    candidate = _validate_type_scores(candidate_scores, field_name="candidate_scores")
    if current.keys() != candidate.keys():
        raise ValueError("비교할 두 사건의 사기 유형 점수 항목이 동일해야 한다.")

    type_codes = sorted(current)
    current_norm = math.sqrt(math.fsum(current[code] ** 2 for code in type_codes))
    candidate_norm = math.sqrt(
        math.fsum(candidate[code] ** 2 for code in type_codes)
    )
    # 영벡터는 방향을 비교할 수 없으므로 유사도 근거로 사용하지 않는다.
    if current_norm == 0.0 or candidate_norm == 0.0:
        return 0.0

    dot_product = math.fsum(
        current[code] * candidate[code] for code in type_codes
    )
    return _round_similarity(dot_product / (current_norm * candidate_norm))


def calculate_risk_grade_similarity(
    current_grade: str,
    candidate_grade: str,
) -> float:
    """위험등급이 정확히 일치하는지를 비교한다."""

    current = _validate_non_empty_string(current_grade, field_name="current_grade")
    candidate = _validate_non_empty_string(
        candidate_grade,
        field_name="candidate_grade",
    )
    return 1.0 if current == candidate else 0.0


def calculate_risk_score_similarity(
    current_score: float,
    candidate_score: float,
    *,
    maximum_risk_score: float = 100.0,
) -> float:
    """최대 위험점수 대비 두 점수의 거리를 0~1 유사도로 변환한다."""

    maximum = _validate_non_negative_number(
        maximum_risk_score,
        field_name="maximum_risk_score",
    )
    if maximum == 0.0:
        raise ValueError("maximum_risk_score는 0보다 커야 한다.")

    current = _validate_range(
        current_score,
        minimum=0.0,
        maximum=maximum,
        field_name="current_score",
    )
    candidate = _validate_range(
        candidate_score,
        minimum=0.0,
        maximum=maximum,
        field_name="candidate_score",
    )
    return _round_similarity(1.0 - abs(current - candidate) / maximum)


def calculate_case_similarity(
    current_case: CaseSimilarityFeatures,
    candidate_case: CaseSimilarityFeatures,
    *,
    config: CaseSimilarityConfig | None = None,
) -> CaseSimilarityResult:
    """항목별 점수를 가중합하여 한 과거 사건의 최종 유사도를 계산한다."""

    applied_config = config or CaseSimilarityConfig()
    current = _validate_case_features(current_case, applied_config)
    candidate = _validate_case_features(candidate_case, applied_config)

    if current.case_id == candidate.case_id:
        raise ValueError("현재 사건과 동일한 사건은 유사도 후보로 사용할 수 없다.")
    if current.type_scores.keys() != candidate.type_scores.keys():
        raise ValueError("비교할 두 사건의 사기 유형 점수 항목이 동일해야 한다.")

    current_evidence = normalize_evidence_codes(current.matched_components)
    candidate_evidence = normalize_evidence_codes(candidate.matched_components)
    candidate_evidence_types = {
        code.split(":", 1)[0] for code in candidate_evidence
    }
    comparable_current_evidence = frozenset(
        code
        for code in current_evidence
        if code.split(":", 1)[0] in candidate_evidence_types
    )
    evidence_similarity = calculate_evidence_similarity(
        comparable_current_evidence,
        candidate_evidence,
    )
    score_vector_similarity = calculate_score_vector_similarity(
        current.type_scores,
        candidate.type_scores,
    )
    risk_grade_similarity = calculate_risk_grade_similarity(
        current.risk_grade,
        candidate.risk_grade,
    )
    risk_score_similarity = calculate_risk_score_similarity(
        current.risk_score,
        candidate.risk_score,
        maximum_risk_score=applied_config.maximum_risk_score,
    )

    weights = applied_config.weights
    similarity_score = _round_similarity(
        weights.evidence * evidence_similarity
        + weights.score_vector * score_vector_similarity
        + weights.risk_grade * risk_grade_similarity
        + weights.risk_score * risk_score_similarity
    )

    return CaseSimilarityResult(
        case_id=candidate.case_id,
        similarity_score=similarity_score,
        evidence_similarity=evidence_similarity,
        score_vector_similarity=score_vector_similarity,
        risk_grade_similarity=risk_grade_similarity,
        risk_score_similarity=risk_score_similarity,
        common_evidence_codes=tuple(
            sorted(comparable_current_evidence & candidate_evidence)
        ),
    )


def rank_similar_cases(
    current_case: CaseSimilarityFeatures,
    candidate_cases: Sequence[CaseSimilarityFeatures],
    *,
    top_k: int = 5,
    config: CaseSimilarityConfig | None = None,
) -> list[CaseSimilarityResult]:
    """기준을 충족하는 과거 사건을 유사도순으로 최대 top_k건 반환한다."""

    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise TypeError("top_k는 정수이어야 한다.")
    if top_k <= 0:
        raise ValueError("top_k는 1 이상이어야 한다.")
    if isinstance(candidate_cases, (str, bytes)) or not isinstance(
        candidate_cases,
        Sequence,
    ):
        raise TypeError("candidate_cases는 사건 목록이어야 한다.")

    applied_config = config or CaseSimilarityConfig()
    current_case_id = _validate_non_empty_string(
        current_case.case_id,
        field_name="current_case.case_id",
    )

    results = [
        calculate_case_similarity(
            current_case,
            candidate,
            config=applied_config,
        )
        for candidate in candidate_cases
        if _validate_non_empty_string(
            candidate.case_id,
            field_name="candidate_case.case_id",
        )
        != current_case_id
    ]
    filtered = [
        result
        for result in results
        if result.similarity_score >= applied_config.minimum_similarity
    ]

    # 동점에서도 실행마다 동일한 결과를 반환하도록 case_id를 보조 기준으로 사용한다.
    filtered.sort(key=lambda result: (-result.similarity_score, result.case_id))
    return filtered[:top_k]


def _validate_case_features(
    case: CaseSimilarityFeatures,
    config: CaseSimilarityConfig,
) -> CaseSimilarityFeatures:
    if not isinstance(case, CaseSimilarityFeatures):
        raise TypeError("사건은 CaseSimilarityFeatures 형식이어야 한다.")

    case_id = _validate_non_empty_string(case.case_id, field_name="case_id")
    type_scores = _validate_type_scores(case.type_scores, field_name="type_scores")
    evidence = normalize_evidence_codes(case.matched_components)
    evidence_type_codes = {code.split(":", 1)[0] for code in evidence}
    if not evidence_type_codes.issubset(type_scores):
        raise ValueError("Rule 근거의 유형 코드는 유형 점수 항목에 포함되어야 한다.")

    risk_score = _validate_range(
        case.risk_score,
        minimum=0.0,
        maximum=config.maximum_risk_score,
        field_name="risk_score",
    )
    risk_grade = _validate_non_empty_string(case.risk_grade, field_name="risk_grade")
    return CaseSimilarityFeatures(
        case_id=case_id,
        type_scores=type_scores,
        matched_components=case.matched_components,
        risk_score=risk_score,
        risk_grade=risk_grade,
    )


def _validate_type_scores(
    scores: Mapping[str, float],
    *,
    field_name: str,
) -> dict[str, float]:
    if not isinstance(scores, Mapping):
        raise TypeError(f"{field_name}는 유형 코드와 점수의 매핑이어야 한다.")
    if not scores:
        raise ValueError(f"{field_name}에는 하나 이상의 유형 점수가 필요하다.")

    validated: dict[str, float] = {}
    for type_code, score in scores.items():
        code = _validate_non_empty_string(type_code, field_name=f"{field_name} 유형 코드")
        validated[code] = _validate_unit_interval(
            score,
            field_name=f"{field_name}[{code}]",
        )
    return validated


def _validate_non_empty_string(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}는 비어 있지 않은 문자열이어야 한다.")
    return value.strip()


def _validate_unit_interval(value: Real, *, field_name: str) -> float:
    return _validate_range(
        value,
        minimum=0.0,
        maximum=1.0,
        field_name=field_name,
    )


def _validate_non_negative_number(value: Real, *, field_name: str) -> float:
    number = _validate_finite_number(value, field_name=field_name)
    if number < 0.0:
        raise ValueError(f"{field_name}는 0 이상이어야 한다.")
    return number


def _validate_range(
    value: Real,
    *,
    minimum: float,
    maximum: float,
    field_name: str,
) -> float:
    number = _validate_finite_number(value, field_name=field_name)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field_name}는 {minimum}~{maximum} 범위여야 한다.")
    return number


def _validate_finite_number(value: Real, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name}는 숫자여야 한다.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name}는 유한한 숫자여야 한다.")
    return number


def _round_similarity(value: float) -> float:
    # 부동소수점 오차가 정렬 및 임계값 판정에 영향을 주지 않도록 정규화한다.
    return round(min(1.0, max(0.0, value)), 10)


__all__ = [
    "CaseSimilarityConfig",
    "CaseSimilarityFeatures",
    "CaseSimilarityResult",
    "CaseSimilarityWeights",
    "calculate_case_similarity",
    "calculate_evidence_similarity",
    "calculate_risk_grade_similarity",
    "calculate_risk_score_similarity",
    "calculate_score_vector_similarity",
    "normalize_evidence_codes",
    "rank_similar_cases",
]
