import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.dto.fraud_detection import FraudDetectionResponseDTO
from app.dto.transaction import TransactionResponseDTO


class TransactionResponseContractTest(unittest.TestCase):
    def test_doo_response_keeps_completed_and_declined_statuses(self) -> None:
        completed = TransactionResponseDTO(
            transaction_id=1,
            prediction_status="COMPLETED",
            predict_proba=0.1,
            message="거래가 승인 되었습니다.",
        )
        declined = TransactionResponseDTO(
            transaction_id=2,
            prediction_status="DECLINED",
            predict_proba=0.91,
            message="이상거래 의심으로 거래가 거절되었습니다.",
        )

        self.assertEqual(completed.prediction_status, "COMPLETED")
        self.assertEqual(declined.prediction_status, "DECLINED")

        with self.assertRaises(ValidationError):
            TransactionResponseDTO(
                transaction_id=3,
                prediction_status="FAILED",
            )

    def test_operational_response_adds_ml_rule_and_label_fields(self) -> None:
        response = FraudDetectionResponseDTO(
            transaction_id=2,
            prediction_status="DECLINED",
            predict_proba=0.91,
            message="이상거래 의심으로 거래가 거절되었습니다.",
            predict_result=True,
            rule_set_id=1,
            rule_scores={"VOICE_PHISHING": 0.3},
            created_at=datetime.now(UTC),
        )

        self.assertTrue(response.predict_result)
        self.assertEqual(response.rule_set_id, 1)
        self.assertEqual(response.rule_scores, {"VOICE_PHISHING": 0.3})

    def test_transaction_id_rejects_the_old_string_contract(self) -> None:
        with self.assertRaises(ValidationError):
            TransactionResponseDTO.model_validate(
                {
                    "transaction_id": "1",
                    "prediction_status": "COMPLETED",
                }
            )


if __name__ == "__main__":
    unittest.main()
