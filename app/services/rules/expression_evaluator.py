"""JSON으로 저장되는 룰 조건식을 안전하게 검증하고 평가한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.rules.feature_builder import RULE_EVALUATION_FIELDS


class RuleExpressionError(ValueError):
    """지원하지 않거나 잘못된 룰 조건식을 발견했을 때 발생한다."""


GROUP_OPERATORS = frozenset({"AND", "OR"})
LEAF_OPERATORS = frozenset(
    {"EQ", "NE", "GT", "GTE", "LT", "LTE", "IN", "BETWEEN"}
)


class RuleExpressionEvaluator:
    """허용 목록 기반으로 조건식을 평가한다.

    Python ``eval``이나 동적 SQL을 사용하지 않으며, 중첩 깊이와 그룹 크기도
    제한해서 관리자가 저장한 조건식이 서버 자원을 과도하게 쓰지 않게 한다.
    """

    def __init__(
        self,
        *,
        allowed_fields: frozenset[str] = RULE_EVALUATION_FIELDS,
        max_depth: int = 8,
        max_group_size: int = 32,
    ) -> None:
        self.allowed_fields = allowed_fields
        self.max_depth = max_depth
        self.max_group_size = max_group_size

    def validate(self, expression: Mapping[str, Any]) -> None:
        self._validate_node(expression, depth=0)

    def evaluate(
        self,
        expression: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> bool:
        self.validate(expression)
        return self.evaluate_validated(expression, context)

    def evaluate_validated(
        self,
        expression: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> bool:
        """이미 검증한 조건식을 다시 순회하지 않고 평가한다."""

        return self._evaluate_node(expression, context)

    def _validate_node(self, expression: Mapping[str, Any], *, depth: int) -> None:
        if not isinstance(expression, Mapping):
            raise RuleExpressionError("조건식 노드는 객체여야 합니다.")
        if depth > self.max_depth:
            raise RuleExpressionError("조건식의 최대 중첩 깊이를 초과했습니다.")

        operator = expression.get("operator")
        if not isinstance(operator, str):
            raise RuleExpressionError("조건식 operator는 문자열이어야 합니다.")

        if operator in GROUP_OPERATORS:
            if set(expression) != {"operator", "conditions"}:
                raise RuleExpressionError(
                    "AND/OR 조건에는 operator와 conditions만 허용됩니다."
                )
            conditions = expression["conditions"]
            if not self._is_sequence(conditions):
                raise RuleExpressionError("conditions는 조건식 배열이어야 합니다.")
            if not conditions:
                raise RuleExpressionError("AND/OR 그룹은 하나 이상의 조건이 필요합니다.")
            if len(conditions) > self.max_group_size:
                raise RuleExpressionError("한 그룹의 조건 개수 제한을 초과했습니다.")
            for condition in conditions:
                self._validate_node(condition, depth=depth + 1)
            return

        if operator not in LEAF_OPERATORS:
            raise RuleExpressionError(f"지원하지 않는 연산자입니다: {operator}")
        if set(expression) != {"field", "operator", "value"}:
            raise RuleExpressionError(
                "비교 조건에는 field, operator, value만 허용됩니다."
            )

        field = expression["field"]
        if not isinstance(field, str) or field not in self.allowed_fields:
            raise RuleExpressionError(f"허용되지 않은 룰 피처입니다: {field}")

        expected = expression["value"]
        if operator == "IN" and not self._is_sequence(expected):
            raise RuleExpressionError("IN의 value는 배열이어야 합니다.")
        if operator == "IN" and not expected:
            raise RuleExpressionError("IN의 value는 비어 있을 수 없습니다.")
        if operator == "BETWEEN":
            if not self._is_sequence(expected) or len(expected) != 2:
                raise RuleExpressionError("BETWEEN의 value는 두 값의 배열이어야 합니다.")

    def _evaluate_node(
        self,
        expression: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> bool:
        operator = expression["operator"]
        if operator == "AND":
            return all(
                self._evaluate_node(condition, context)
                for condition in expression["conditions"]
            )
        if operator == "OR":
            return any(
                self._evaluate_node(condition, context)
                for condition in expression["conditions"]
            )

        field = expression["field"]
        if field not in context:
            raise RuleExpressionError(f"평가 컨텍스트에 피처가 없습니다: {field}")
        actual = context[field]
        expected = expression["value"]

        # 룰 DTO는 비교값 null과 IS NULL 연산자를 지원하지 않는다. 따라서
        # 결측값은 NE를 포함한 어떤 조건에도 매칭시키지 않는다. 그렇지 않으면
        # `account_balance NE 0` 같은 사용자 룰이 결측 행을 잘못 적중시킨다.
        if actual is None:
            return False

        try:
            if operator == "EQ":
                return actual == expected
            if operator == "NE":
                return actual != expected
            if operator == "GT":
                return actual > expected
            if operator == "GTE":
                return actual >= expected
            if operator == "LT":
                return actual < expected
            if operator == "LTE":
                return actual <= expected
            if operator == "IN":
                return actual in expected
            if operator == "BETWEEN":
                minimum, maximum = expected
                return minimum <= actual <= maximum
        except TypeError as exc:
            raise RuleExpressionError(
                f"{field}에 {operator} 비교를 적용할 수 없습니다."
            ) from exc

        raise RuleExpressionError(f"지원하지 않는 연산자입니다: {operator}")

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


__all__ = [
    "GROUP_OPERATORS",
    "LEAF_OPERATORS",
    "RuleExpressionError",
    "RuleExpressionEvaluator",
]
