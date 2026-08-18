import unittest
from datetime import datetime
from unittest.mock import Mock

from app.data.model.transaction import TransactionStatus
from app.dto.transaction import TransactionCreateDTO
from app.services.ml_serving.client import MLPredictionResponse
from app.services.transaction.transaction_service import TransactionService


def _transaction() -> TransactionCreateDTO:
    return TransactionCreateDTO(
        customer_id=1,
        source_account_number="10000001",
        recipient_account_number="20000001",
        transaction_datetime=datetime(2026, 8, 18, 12, 0),
        transaction_amount=10_000,
        channel="mobile",
        type_general_automatic="general",
        initial_balance=100_000,
        balance=90_000,
        rooting_jailbreak_indicator=False,
        mobile_roaming_indicator=False,
        vpn_indicator=False,
        flag_terminal_malicious_behavior_1=False,
        flag_terminal_malicious_behavior_2=False,
        flag_terminal_malicious_behavior_3=False,
        flag_terminal_malicious_behavior_5=False,
        flag_terminal_malicious_behavior_6=False,
    )


def _prediction(*, result: int, probability: float) -> MLPredictionResponse:
    return MLPredictionResponse(
        predict_result=result,
        predict_proba=probability,
        shap_values={},
        model_name="fdshield-fraud-detector",
        model_version="3",
    )


class TransactionServiceTest(unittest.TestCase):
    def test_probability_at_threshold_declines_transaction(self) -> None:
        repository = Mock()
        repository.save_transaction.side_effect = lambda transaction: transaction
        service = TransactionService(repository)

        transaction, is_fraud = service.save_transaction(
            _transaction(),
            _prediction(result=0, probability=0.5),
        )

        self.assertTrue(is_fraud)
        self.assertEqual(transaction.transaction_status, TransactionStatus.DECLINED)
        self.assertEqual(transaction.error_code, "FRAUD")
        self.assertEqual(transaction.balance, transaction.initial_balance)
        repository.update_source_balance.assert_not_called()

    def test_probability_below_threshold_approves_transaction(self) -> None:
        repository = Mock()
        repository.save_transaction.side_effect = lambda transaction: transaction
        service = TransactionService(repository)

        transaction, is_fraud = service.save_transaction(
            _transaction(),
            _prediction(result=1, probability=0.49),
        )

        self.assertFalse(is_fraud)
        self.assertEqual(transaction.transaction_status, TransactionStatus.APPROVED)
        repository.update_source_balance.assert_called_once_with(
            "10000001",
            90_000,
        )


if __name__ == "__main__":
    unittest.main()
