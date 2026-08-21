"""최근 ML 양성 거래에서 패턴별 참고 통계를 계산한다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from statistics import fmean, median
from typing import Any, Literal

from sqlmodel import Session

from app.repositories.transaction import PredictionResultRepository
from app.services.features.ml_feature_assembler import assemble_ml_features
from app.services.rules.engine import RuleEngine
from app.services.rules.expression_evaluator import RuleExpressionEvaluator


@dataclass(frozen=True, slots=True)
class PatternStatisticsDefinition:
    component_key: str
    condition_expression: Mapping[str, Any]
    feature_field: str | None
    feature_value_type: Literal["integer", "number", "boolean", "enum"] | None


@dataclass(frozen=True, slots=True)
class PatternValueCount:
    value: str | int | bool
    count: int
    rate: float | None


@dataclass(frozen=True, slots=True)
class PatternFeatureStatistics:
    field: str
    value_type: Literal["integer", "number", "boolean", "enum"]
    value_count: int
    average: float | None
    median: float | None
    p90: float | None
    value_counts: list[PatternValueCount]


@dataclass(frozen=True, slots=True)
class PatternStatistics:
    component_key: str
    matched_count: int
    matched_rate: float | None
    feature_statistics: PatternFeatureStatistics | None


@dataclass(frozen=True, slots=True)
class PatternStatisticsResult:
    requested_count: int
    sample_count: int
    has_more: bool
    patterns: list[PatternStatistics]


def _numeric_statistics(
    *,
    field: str,
    value_type: Literal["integer", "number"],
    values: Sequence[Any],
) -> PatternFeatureStatistics:
    numeric_values = [float(value) for value in values]
    ordered = sorted(numeric_values)
    value_count = len(ordered)
    return PatternFeatureStatistics(
        field=field,
        value_type=value_type,
        value_count=value_count,
        average=round(fmean(ordered), 10) if ordered else None,
        median=round(float(median(ordered)), 10) if ordered else None,
        p90=(
            round(ordered[ceil(value_count * 0.9) - 1], 10)
            if ordered
            else None
        ),
        value_counts=[],
    )


def _categorical_statistics(
    *,
    field: str,
    value_type: Literal["boolean", "enum"],
    values: Sequence[Any],
) -> PatternFeatureStatistics:
    counts = Counter(values)
    value_count = len(values)
    value_counts = [
        PatternValueCount(
            value=value,
            count=count,
            rate=round(count / value_count, 10) if value_count else None,
        )
        for value, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], str(item[0])),
        )
    ]
    return PatternFeatureStatistics(
        field=field,
        value_type=value_type,
        value_count=value_count,
        average=None,
        median=None,
        p90=None,
        value_counts=value_counts,
    )


def calculate_pattern_statistics(
    *,
    session: Session,
    definitions: Sequence[PatternStatisticsDefinition],
    sample_size: int,
) -> PatternStatisticsResult:
    """같은 거래 표본으로 모든 패턴의 매칭률과 Feature 분포를 계산한다."""

    selected, has_more = PredictionResultRepository(
        session
    ).latest_positive_feature_rows(limit=sample_size)
    engine = RuleEngine()
    evaluator = RuleExpressionEvaluator()
    for definition in definitions:
        evaluator.validate(definition.condition_expression)

    contexts = [
        engine.feature_builder.build(
            assemble_ml_features(
                customer=customer,
                source_account=source_account,
                recipient_account=recipient_account,
                transaction=transaction,
                derived=derived,
            )
        )
        for transaction, customer, source_account, recipient_account, derived in selected
    ]
    sample_count = len(contexts)
    pattern_results: list[PatternStatistics] = []

    for definition in definitions:
        matched_count = sum(
            evaluator.evaluate_validated(definition.condition_expression, context)
            for context in contexts
        )
        feature_statistics = None
        if definition.feature_field and definition.feature_value_type:
            values = [
                context[definition.feature_field]
                for context in contexts
                if context[definition.feature_field] is not None
            ]
            if definition.feature_value_type in {"integer", "number"}:
                feature_statistics = _numeric_statistics(
                    field=definition.feature_field,
                    value_type=definition.feature_value_type,
                    values=values,
                )
            else:
                feature_statistics = _categorical_statistics(
                    field=definition.feature_field,
                    value_type=definition.feature_value_type,
                    values=values,
                )

        pattern_results.append(
            PatternStatistics(
                component_key=definition.component_key,
                matched_count=matched_count,
                matched_rate=(
                    round(matched_count / sample_count, 10) if sample_count else None
                ),
                feature_statistics=feature_statistics,
            )
        )

    return PatternStatisticsResult(
        requested_count=sample_size,
        sample_count=sample_count,
        has_more=has_more,
        patterns=pattern_results,
    )


__all__ = [
    "PatternFeatureStatistics",
    "PatternStatistics",
    "PatternStatisticsDefinition",
    "PatternStatisticsResult",
    "PatternValueCount",
    "calculate_pattern_statistics",
]
