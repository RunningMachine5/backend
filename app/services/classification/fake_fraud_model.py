from app.dto.fraud import FraudPredictionDTO
from app.dto.transaction import TransactionDTO


class FakeFraudModel:
    """외부 모델 대신 최소 규칙으로 사기 확률을 계산하는 Fake 모델."""

    def predict(self, transaction: TransactionDTO) -> FraudPredictionDTO:
        """거래 금액, 편차, 결제수단, 업종 점수를 더해 이진 분류한다."""
        amount_score = 0.45 if transaction.amount >= 50_000_000 else 0.25
        deviation_ratio = transaction.amount / transaction.user_amount_std_dev
        deviation_score = 0.35 if deviation_ratio >= 100 else 0.20
        payment_score = 0.10 if transaction.payment_method == "CARD" else 0.00
        merchant_score = 0.10 if transaction.merchant_category == "VEHICLES" else 0.00

        probability = min(
            amount_score + deviation_score + payment_score + merchant_score,
            1.0,
        )
        return FraudPredictionDTO(
            is_fraud=probability >= 0.55,
            fraud_probability=round(probability, 2),
        )

