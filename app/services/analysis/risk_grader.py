from app.domain.enums import RiskGrade
from app.dto.fraud import RiskAssessmentDTO


class RiskGrader:
    """거래금액 위험도와 ML 사기확률로 대응 우선순위를 계산한다."""

    AMOUNT_WEIGHT = 0.60
    ML_PROBABILITY_WEIGHT = 0.40

    def assess(
        self,
        transaction_amount: int,
        fraud_probability: float,
    ) -> RiskAssessmentDTO:
        """금액 위험도 60%와 ML 사기확률 40%의 가중합을 계산한다."""
        if not 0.0 <= fraud_probability <= 1.0:
            raise ValueError("fraud_probability는 0.0~1.0 범위여야 한다.")

        absolute_amount = abs(transaction_amount)
        amount_risk_factor = self.calculate_amount_risk_factor(absolute_amount)

        # 두 입력을 각각 최대 60점과 40점으로 환산하여 100점 만점으로 합산한다.
        amount_points = 100 * self.AMOUNT_WEIGHT * amount_risk_factor
        ml_points = 100 * self.ML_PROBABILITY_WEIGHT * fraud_probability
        risk_score = min(100, round(amount_points + ml_points))
        risk_grade = self.grade(risk_score)

        return RiskAssessmentDTO(
            risk_score=risk_score,
            risk_grade=risk_grade,
            risk_reasons=[
                (
                    f"절대 거래금액 {absolute_amount:,}원의 금액 위험도는 "
                    f"{amount_risk_factor:.2f}이며 기여점수는 {amount_points:.1f}점임"
                ),
                (
                    f"ML 사기확률은 {fraud_probability:.2f}이며 "
                    f"기여점수는 {ml_points:.1f}점임"
                ),
            ],
        )

    @staticmethod
    def calculate_amount_risk_factor(transaction_amount: int) -> float:
        """테스트 데이터 분포를 반영한 금액 구간별 위험도를 반환한다."""
        absolute_amount = abs(transaction_amount)

        if absolute_amount >= 30_000_000:
            return 1.00
        if absolute_amount >= 10_000_000:
            return 0.80
        if absolute_amount >= 5_000_000:
            return 0.50
        if absolute_amount >= 1_000_000:
            return 0.30
        return 0.10

    @staticmethod
    def grade(risk_score: int) -> RiskGrade:
        """100점 만점의 위험점수를 네 단계 위험등급으로 변환한다."""
        if not 0 <= risk_score <= 100:
            raise ValueError("risk_score는 0~100 범위여야 한다.")

        if risk_score >= 80:
            return RiskGrade.VERY_HIGH
        if risk_score >= 60:
            return RiskGrade.HIGH
        if risk_score >= 40:
            return RiskGrade.MEDIUM
        return RiskGrade.LOW

