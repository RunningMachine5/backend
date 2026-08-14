import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.dto.transaction import TransactionResponseDTO


class TransactionResponseContractTest(unittest.TestCase):
    def test_prediction_statuses_and_new_result_names_are_supported(self) -> None:
        unavailable = TransactionResponseDTO(
            transaction_id=1,
            prediction_status="NOT_AVAILABLE",
            created_at=datetime.now(UTC),
        )
        completed = TransactionResponseDTO(
            transaction_id=2,
            prediction_status="COMPLETED",
            predict_result=True,
            predict_proba=0.91,
            created_at=datetime.now(UTC),
        )

        self.assertIsNone(unavailable.predict_result)
        self.assertIsNone(unavailable.predict_proba)
        self.assertTrue(completed.predict_result)
        self.assertEqual(completed.predict_proba, 0.91)

    def test_transaction_id_rejects_the_old_string_contract(self) -> None:
        with self.assertRaises(ValidationError):
            TransactionResponseDTO.model_validate(
                {
                    "transaction_id": "1",
                    "prediction_status": "NOT_AVAILABLE",
                    "created_at": datetime.now(UTC),
                }
            )


if __name__ == "__main__":
    unittest.main()
