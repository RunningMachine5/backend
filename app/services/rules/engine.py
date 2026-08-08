"""동적으로 등록된 사기유형 룰의 점수를 모두 계산한다."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True, slots=True)
class RuleScoreResult:
    type_scores: dict[str, float]
    matched_components: dict[str, list[str]]
    rule_set_version: str


_TYPE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_COMPONENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class RuleEngine:
    """활성 룰별 점수와 일치한 구성요소를 계산한다."""

    def __init__(
        self,
        *,
        feature_builder: RuleFeatureBuilder | None = None,
        expression_evaluator: RuleExpressionEvaluator | None = None,
    ) -> None:
        self.feature_builder = feature_builder or RuleFeatureBuilder()
        self.expression_evaluator = expression_evaluator or RuleExpressionEvaluator()

    def score(
        self,
        raw_data: Mapping[str, Any],
        rule_set: RuleSetDefinition | None = None,
    ) -> RuleScoreResult:
        if rule_set is None:
            from app.services.rules.defaults import DEFAULT_RULE_SET

            rule_set = DEFAULT_RULE_SET
        context = self.feature_builder.build(raw_data)
        return self.score_context(context, rule_set)

    def score_context(
        self,
        context: Mapping[str, Any],
        rule_set: RuleSetDefinition,
    ) -> RuleScoreResult:
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

        return RuleScoreResult(
            type_scores=type_scores,
            matched_components=matched_components,
            rule_set_version=rule_set.version,
        )

    def validate_rule_set(self, rule_set: RuleSetDefinition) -> None:
        if not rule_set.version.strip():
            raise RuleSetValidationError("룰셋 version은 비어 있을 수 없습니다.")
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

__all__ = [
    "FraudRuleDefinition",
    "RuleScoreResult",
    "RuleComponentDefinition",
    "RuleEngine",
    "RuleSetDefinition",
    "RuleSetValidationError",
]
