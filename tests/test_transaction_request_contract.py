import json
import unittest
from datetime import timedelta
from pathlib import Path

from app.dto.transaction import TransactionRequestDTO


class TransactionRequestContractTest(unittest.TestCase):
    def test_documented_example_matches_slim_request_contract(self) -> None:
        example_path = (
            Path(__file__).parents[1] / "examples" / "transaction-request.json"
        )
        payload = json.loads(example_path.read_text(encoding="utf-8"))

        request = TransactionRequestDTO.model_validate(payload)

        self.assertIsNone(request.customer_id)
        self.assertEqual(request.source_account_number, "123456789400")
        self.assertEqual(request.transaction_amount, 75_000)
        self.assertEqual(request.transaction_datetime.utcoffset(), timedelta(hours=9))

    def test_naive_transaction_datetime_is_interpreted_as_kst(self) -> None:
        example_path = (
            Path(__file__).parents[1] / "examples" / "transaction-request.json"
        )
        payload = json.loads(example_path.read_text(encoding="utf-8"))
        payload["transaction_datetime"] = "2025-01-01T00:02:00"

        request = TransactionRequestDTO.model_validate(payload)

        self.assertEqual(request.transaction_datetime.utcoffset(), timedelta(hours=9))


if __name__ == "__main__":
    unittest.main()
