from app.dto.fraud import FraudPredictionDTO
from app.dto.transaction import TransactionDTO, TransactionFeaturesDTO


class FakeFraudModel:
    """외부 모델 대신 최소 규칙으로 사기 확률을 계산하는 Fake 모델."""

    def predict(self, transaction: TransactionDTO) -> TransactionFeaturesDTO:
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
        # return FraudPredictionDTO(
        #     is_fraud=probability >= 0.55,
        #     fraud_probability=round(probability, 2),
        #
        # )
        return TransactionFeaturesDTO(
            user_id = transaction.user_id,
            is_fraud = True,
            high_relevance_feature={
                "amount":transaction.amount,
                "deviation_ratio":deviation_ratio,
                "merchant_category":transaction.merchant_category,
                "transaction_time": transaction.transaction_time,
                "payment_method":transaction.payment_method,
            },# 지금은 하드 코딩. 나중엔 연관도 상위 피쳐들을 리턴.
            fraud_probability=round(probability, 2),
        )