import unittest

from app.dto.transaction import TransactionDTO
from app.services.classification.fake_fraud_model import FakeFraudModel


class FakeFraudModelTest(unittest.TestCase):
    """Fake 모델 스켈레톤의 점수 계산과 피처 변환을 검증한다."""

    def test_predict_calculates_probability_and_features(self) -> None:
        transaction = TransactionDTO(
            user_id="USR_SAFE",
            user_name="홍길동",
            email="sample@email.com",
            transaction_time="2026-07-30T14:00:00+09:00",
            amount=100_000,
            user_amount_std_dev=100_000.00,
            payment_method="TRANSFER",
            merchant_category="GROCERIES",
        )

        prediction = FakeFraudModel().predict(transaction)

        self.assertEqual(prediction.user_id, transaction.user_id)
        self.assertEqual(prediction.fraud_probability, 0.45)
        self.assertEqual(
            prediction.high_relevance_feature,
            {
                "amount": 100_000,
                "deviation_ratio": 1.0,
                "merchant_category": "GROCERIES",
                "transaction_time": "2026-07-30T14:00:00+09:00",
                "payment_method": "TRANSFER",
            },
        )


if __name__ == "__main__":
    unittest.main()
