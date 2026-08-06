import unittest

from app.domain.enums import RiskGrade
from app.services.analysis.risk_grader import RiskGrader


class RiskGraderTest(unittest.TestCase):
    """거래금액·ML 사기확률 기반 위험등급 계산을 검증한다."""

    def setUp(self) -> None:
        self.grader = RiskGrader()

    def test_amount_risk_factor_boundaries(self) -> None:
        """각 금액 구간의 시작값에서 올바른 위험도를 반환하는지 확인한다."""
        cases = [
            (999_999, 0.10),
            (1_000_000, 0.30),
            (5_000_000, 0.50),
            (10_000_000, 0.80),
            (30_000_000, 1.00),
        ]

        for amount, expected in cases:
            with self.subTest(amount=amount):
                self.assertEqual(
                    self.grader.calculate_amount_risk_factor(amount),
                    expected,
                )

    def test_negative_amount_uses_absolute_value(self) -> None:
        """출금을 뜻하는 음수 금액도 절댓값 기준으로 계산하는지 확인한다."""
        positive = self.grader.assess(9_450_000, 0.94)
        negative = self.grader.assess(-9_450_000, 0.94)

        self.assertEqual(negative, positive)
        self.assertEqual(negative.risk_score, 68)
        self.assertEqual(negative.risk_grade, RiskGrade.HIGH)

    def test_high_amount_and_probability_is_very_high(self) -> None:
        """금액과 ML 확률이 모두 높은 거래는 매우높음으로 분류한다."""
        result = self.grader.assess(15_000_000, 0.90)

        self.assertEqual(result.risk_score, 84)
        self.assertEqual(result.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(result.amount_risk_factor, 0.80)
        self.assertEqual(result.amount_points, 48.0)
        self.assertEqual(result.ml_probability_points, 36.0)

    def test_small_amount_and_high_probability_is_medium(self) -> None:
        """소액이라도 ML 확률이 높으면 보통 등급까지 상승하는지 확인한다."""
        result = self.grader.assess(500_000, 0.98)

        self.assertEqual(result.risk_score, 45)
        self.assertEqual(result.risk_grade, RiskGrade.MEDIUM)

    def test_risk_grade_boundaries(self) -> None:
        """위험점수 경계값이 네 단계 등급으로 정확히 변환되는지 확인한다."""
        cases = [
            (39, RiskGrade.LOW),
            (40, RiskGrade.MEDIUM),
            (59, RiskGrade.MEDIUM),
            (60, RiskGrade.HIGH),
            (79, RiskGrade.HIGH),
            (80, RiskGrade.VERY_HIGH),
            (100, RiskGrade.VERY_HIGH),
        ]

        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(self.grader.grade(score), expected)

    def test_probability_out_of_range_is_rejected(self) -> None:
        """0~1 범위를 벗어난 ML 사기확률은 계산하지 않는다."""
        for probability in (-0.01, 1.01):
            with self.subTest(probability=probability):
                with self.assertRaisesRegex(ValueError, "0.0~1.0"):
                    self.grader.assess(1_000_000, probability)


if __name__ == "__main__":
    unittest.main()
