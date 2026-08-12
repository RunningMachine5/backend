"""Rule 유형별 점수로 분류 결과의 확실성을 계산한다."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real

from app.domain.agent_status import ClassificationStatus


@dataclass(frozen=True, slots=True)
class TypeConfidenceThresholds:
    """평가 결과에 따라 조정할 수 있는 유형 확실성 임계값."""

    minimum_top_score: float = 0.60
    minimum_margin: float = 0.15

    def __post_init__(self) -> None:
        _validate_unit_interval(
            self.minimum_top_score,
            field_name="minimum_top_score",
        )
        _validate_unit_interval(
            self.minimum_margin,
            field_name="minimum_margin",
        )


@dataclass(frozen=True, slots=True)
class TypeConfidenceResult:
    """1위, 2위 점수와 이를 바탕으로 계산한 분류 상태."""

    top_type_code: str
    top_score: float
    second_type_code: str
    second_score: float
    score_margin: float
    classification_status: ClassificationStatus


def calculate_type_confidence(
    type_scores: Mapping[str, float],
    *,
    thresholds: TypeConfidenceThresholds | None = None,
) -> TypeConfidenceResult:
    """유형 점수의 절대값과 1·2위 차이를 기준으로 확실성을 판정한다."""

    validated_scores = _validate_type_scores(type_scores)
    applied_thresholds = thresholds or TypeConfidenceThresholds()

    # 동점에서도 실행마다 같은 결과를 반환하도록 유형 코드를 보조 정렬 기준으로 둔다.
    ranked_scores = sorted(
        validated_scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    (top_type_code, top_score), (
        second_type_code,
        second_score,
    ) = ranked_scores[:2]

    score_margin = round(top_score - second_score, 10)
    is_ambiguous = (
        top_score < applied_thresholds.minimum_top_score
        or score_margin < applied_thresholds.minimum_margin
    )

    return TypeConfidenceResult(
        top_type_code=top_type_code,
        top_score=top_score,
        second_type_code=second_type_code,
        second_score=second_score,
        score_margin=score_margin,
        classification_status=(
            ClassificationStatus.AMBIGUOUS
            if is_ambiguous
            else ClassificationStatus.CONFIDENT
        ),
    )


def _validate_type_scores(type_scores: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(type_scores, Mapping):
        raise TypeError("type_scores는 유형 코드와 점수의 매핑이어야 한다.")
    if len(type_scores) < 2:
        raise ValueError("type_scores에는 최소 두 개 유형의 점수가 필요하다.")

    validated: dict[str, float] = {}
    for type_code, score in type_scores.items():
        if not isinstance(type_code, str) or not type_code.strip():
            raise ValueError("사기유형 코드는 비어 있지 않은 문자열이어야 한다.")
        validated[type_code] = _validate_unit_interval(
            score,
            field_name=f"type_scores[{type_code}]",
        )

    return validated


def _validate_unit_interval(value: Real, *, field_name: str) -> float:
    # bool은 int의 하위 타입이므로 별도로 제외해야 점수 True/False 입력을 막을 수 있다.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name}는 숫자여야 한다.")

    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name}는 유한한 숫자여야 한다.")
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name}는 0.0~1.0 범위여야 한다.")

    return normalized


__all__ = [
    "ClassificationStatus",
    "TypeConfidenceResult",
    "TypeConfidenceThresholds",
    "calculate_type_confidence",
]
