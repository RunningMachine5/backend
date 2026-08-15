import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.dto.transaction import TransactionResponseDTO


class TransactionResponseContractTest(unittest.TestCase):
    def test_prediction_statuses_and_new_result_names_are_supported(self) -> None:
        failed = TransactionResponseDTO(
            transaction_id=1,
            prediction_status="FAILED",
            created_at=datetime.now(UTC),
        )
        completed = TransactionResponseDTO(
            transaction_id=2,
            prediction_status="COMPLETED",
            predict_result=True,
            predict_proba=0.91,
            rule_set_id=1,
            rule_scores={"VOICE_PHISHING": 0.3},
            created_at=datetime.now(UTC),
        )

        self.assertIsNone(failed.predict_result)
        self.assertIsNone(failed.predict_proba)
        self.assertTrue(completed.predict_result)
        self.assertEqual(completed.predict_proba, 0.91)
        self.assertEqual(completed.rule_set_id, 1)
        self.assertEqual(completed.rule_scores, {"VOICE_PHISHING": 0.3})

        with self.assertRaises(ValidationError):
            TransactionResponseDTO(
                transaction_id=3,
                prediction_status="NOT_AVAILABLE",
                created_at=datetime.now(UTC),
            )

    def test_transaction_id_rejects_the_old_string_contract(self) -> None:
        with self.assertRaises(ValidationError):
            TransactionResponseDTO.model_validate(
                {
                    "transaction_id": "1",
                    "prediction_status": "FAILED",
                    "created_at": datetime.now(UTC),
                }
            )


if __name__ == "__main__":
    unittest.main()
