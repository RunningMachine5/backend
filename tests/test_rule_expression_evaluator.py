import unittest

from app.services.rules.expression_evaluator import (
    RuleExpressionError,
    RuleExpressionEvaluator,
)


class RuleExpressionEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = RuleExpressionEvaluator()

    def test_evaluates_nested_and_or_expression(self) -> None:
        expression = {
            "operator": "AND",
            "conditions": [
                {
                    "field": "loan_related",
                    "operator": "EQ",
                    "value": True,
                },
                {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "transaction_age",
                            "operator": "BETWEEN",
                            "value": [30, 49],
                        },
                        {
                            "field": "transaction_age",
                            "operator": "GTE",
                            "value": 60,
                        },
                    ],
                },
            ],
        }

        self.assertTrue(
            self.evaluator.evaluate(
                expression,
                {"loan_related": True, "transaction_age": 40},
            )
        )
        self.assertFalse(
            self.evaluator.evaluate(
                expression,
                {"loan_related": False, "transaction_age": 40},
            )
        )

    def test_supports_in_and_comparison_operators(self) -> None:
        context = {"Customer_loan_type": "c", "transaction_age": 65}

        self.assertTrue(
            self.evaluator.evaluate(
                {
                    "field": "Customer_loan_type",
                    "operator": "IN",
                    "value": ["b", "c", "d", "e"],
                },
                context,
            )
        )
        self.assertTrue(
            self.evaluator.evaluate(
                {
                    "field": "transaction_age",
                    "operator": "GT",
                    "value": 60,
                },
                context,
            )
        )

    def test_rejects_unknown_field_and_operator(self) -> None:
        with self.assertRaisesRegex(RuleExpressionError, "허용되지 않은 룰 피처"):
            self.evaluator.validate(
                {"field": "__class__", "operator": "EQ", "value": True}
            )

        with self.assertRaisesRegex(RuleExpressionError, "지원하지 않는 연산자"):
            self.evaluator.validate(
                {
                    "field": "transaction_age",
                    "operator": "PYTHON",
                    "value": "__import__('os')",
                }
            )

    def test_rejects_extra_expression_keys(self) -> None:
        with self.assertRaisesRegex(RuleExpressionError, "만 허용"):
            self.evaluator.validate(
                {
                    "field": "transaction_age",
                    "operator": "GTE",
                    "value": 60,
                    "script": "do_not_run",
                }
            )

    def test_rejects_empty_group(self) -> None:
        with self.assertRaisesRegex(RuleExpressionError, "하나 이상의 조건"):
            self.evaluator.validate({"operator": "AND", "conditions": []})


if __name__ == "__main__":
    unittest.main()
