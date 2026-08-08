"""동적으로 등록된 사기유형 룰의 점수를 계산하고 최종 유형을 선택한다."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.services.rules.expression_evaluator import RuleExpressionEvaluator
from app.services.rules.feature_builder import RuleFeatureBuilder


class RuleSetValidationError(ValueError):
    """실행할 수 없는 룰셋을 발견했을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class RuleComponentDefinition:
    component_key: str
    name: str
    condition_expression: Mapping[str, Any]
    weight: float


@dataclass(frozen=True, slots=True)
class FraudRuleDefinition:
    type_code: str
    display_name: str
    components: tuple[RuleComponentDefinition, ...]
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RuleSetDefinition:
    version: str
    rules: tuple[FraudRuleDefinition, ...]
    minimum_score: float = 0.50
    ambiguity_margin: float = 0.10


@dataclass(frozen=True, slots=True)
class RuleClassificationResult:
    status: Literal["CLASSIFIED", "UNCLASSIFIED"]
    fraud_type: str | None
    top_score: float
    second_score: float
    score_gap: float
    type_scores: dict[str, float]
    matched_components: dict[str, list[str]]
    rule_set_version: str
    decision_reason: Literal[
        "CLASSIFIED",
        "BELOW_MINIMUM_SCORE",
        "AMBIGUOUS_TOP_SCORES",
    ]


_TYPE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_COMPONENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class RuleEngine:
    """룰별 점수를 계산하고 최소점수·점수 차이 방식으로 분류한다."""

    def __init__(
        self,
        *,
        feature_builder: RuleFeatureBuilder | None = None,
        expression_evaluator: RuleExpressionEvaluator | None = None,
    ) -> None:
        self.feature_builder = feature_builder or RuleFeatureBuilder()
        self.expression_evaluator = expression_evaluator or RuleExpressionEvaluator()

    def classify(
        self,
        raw_data: Mapping[str, Any],
        rule_set: RuleSetDefinition | None = None,
    ) -> RuleClassificationResult:
        if rule_set is None:
            from app.services.rules.defaults import DEFAULT_RULE_SET

            rule_set = DEFAULT_RULE_SET
        context = self.feature_builder.build(raw_data)
        return self.classify_context(context, rule_set)

    def classify_context(
        self,
        context: Mapping[str, Any],
        rule_set: RuleSetDefinition,
    ) -> RuleClassificationResult:
        self.validate_rule_set(rule_set)

        type_scores: dict[str, float] = {}
        matched_components: dict[str, list[str]] = {}
        for rule in rule_set.rules:
            if not rule.enabled:
                continue

            matched: list[str] = []
            matched_weights: list[float] = []
            for component in rule.components:
                if self.expression_evaluator.evaluate(
                    component.condition_expression,
                    context,
                ):
                    matched.append(component.component_key)
                    matched_weights.append(component.weight)

            type_scores[rule.type_code] = round(math.fsum(matched_weights), 10)
            matched_components[rule.type_code] = matched

        ranked = sorted(type_scores.items(), key=lambda item: item[1], reverse=True)
        top_type, top_score = ranked[0]
        second_score = ranked[1][1]
        score_gap = round(top_score - second_score, 10)

        if top_score < rule_set.minimum_score:
            status: Literal["CLASSIFIED", "UNCLASSIFIED"] = "UNCLASSIFIED"
            fraud_type = None
            decision_reason = "BELOW_MINIMUM_SCORE"
        elif score_gap < rule_set.ambiguity_margin:
            status = "UNCLASSIFIED"
            fraud_type = None
            decision_reason = "AMBIGUOUS_TOP_SCORES"
        else:
            status = "CLASSIFIED"
            fraud_type = top_type
            decision_reason = "CLASSIFIED"

        return RuleClassificationResult(
            status=status,
            fraud_type=fraud_type,
            top_score=top_score,
            second_score=second_score,
            score_gap=score_gap,
            type_scores=type_scores,
            matched_components=matched_components,
            rule_set_version=rule_set.version,
            decision_reason=decision_reason,
        )

    def validate_rule_set(self, rule_set: RuleSetDefinition) -> None:
        if not rule_set.version.strip():
            raise RuleSetValidationError("룰셋 version은 비어 있을 수 없습니다.")
        self._validate_probability("minimum_score", rule_set.minimum_score)
        self._validate_probability("ambiguity_margin", rule_set.ambiguity_margin)

        enabled_rules = [rule for rule in rule_set.rules if rule.enabled]
        if len(enabled_rules) < 2:
            raise RuleSetValidationError("활성 룰은 최소 두 개 이상이어야 합니다.")

        type_codes = [rule.type_code for rule in enabled_rules]
        if len(type_codes) != len(set(type_codes)):
            raise RuleSetValidationError("활성 룰의 type_code는 중복될 수 없습니다.")

        for rule in enabled_rules:
            self._validate_rule(rule)

    def _validate_rule(self, rule: FraudRuleDefinition) -> None:
        if not _TYPE_CODE_PATTERN.fullmatch(rule.type_code):
            raise RuleSetValidationError(
                f"올바르지 않은 사기유형 코드입니다: {rule.type_code}"
            )
        if not rule.display_name.strip():
            raise RuleSetValidationError("사기유형 표시 이름은 비어 있을 수 없습니다.")
        if not rule.components:
            raise RuleSetValidationError(
                f"{rule.type_code} 룰에는 하나 이상의 구성요소가 필요합니다."
            )

        component_keys = [component.component_key for component in rule.components]
        if len(component_keys) != len(set(component_keys)):
            raise RuleSetValidationError(
                f"{rule.type_code}의 component_key는 중복될 수 없습니다."
            )

        weights: list[float] = []
        for component in rule.components:
            if not _COMPONENT_KEY_PATTERN.fullmatch(component.component_key):
                raise RuleSetValidationError(
                    f"올바르지 않은 component_key입니다: {component.component_key}"
                )
            if not component.name.strip():
                raise RuleSetValidationError("룰 구성요소 이름은 비어 있을 수 없습니다.")
            if isinstance(component.weight, bool) or not isinstance(
                component.weight,
                (int, float),
            ):
                raise RuleSetValidationError("가중치는 숫자여야 합니다.")
            if not 0 < component.weight <= 1:
                raise RuleSetValidationError("가중치는 0보다 크고 1 이하여야 합니다.")
            self.expression_evaluator.validate(component.condition_expression)
            weights.append(float(component.weight))

        if not math.isclose(math.fsum(weights), 1.0, abs_tol=1e-9):
            raise RuleSetValidationError(
                f"{rule.type_code}의 가중치 합계는 1.0이어야 합니다."
            )

    @staticmethod
    def _validate_probability(name: str, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuleSetValidationError(f"{name}은 숫자여야 합니다.")
        if not 0 <= value <= 1:
            raise RuleSetValidationError(f"{name}은 0과 1 사이여야 합니다.")


__all__ = [
    "FraudRuleDefinition",
    "RuleClassificationResult",
    "RuleComponentDefinition",
    "RuleEngine",
    "RuleSetDefinition",
    "RuleSetValidationError",
]
