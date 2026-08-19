import unittest
from datetime import UTC, datetime
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
        transaction_datetime=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
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
    @staticmethod
    def _repository() -> Mock:
        repository = Mock()

        def save(transaction):
            transaction.id = 7
            return transaction

        repository.save_transaction.side_effect = save
        return repository

    def test_probability_at_threshold_declines_transaction(self) -> None:
        repository = self._repository()
        service = TransactionService(repository)

        prediction = _prediction(result=0, probability=0.5)
        saved_prediction, response = service.save_transaction(
            _transaction(),
            prediction,
        )
        transaction = repository.save_transaction.call_args.args[0]

        self.assertIs(saved_prediction, prediction)
        self.assertEqual(transaction.transaction_status, TransactionStatus.DECLINED)
        self.assertEqual(transaction.error_code, "f")
        self.assertEqual(transaction.balance, transaction.initial_balance)
        self.assertEqual(prediction.transaction_id, 7)
        self.assertEqual(response.prediction_status, "DECLINED")
        self.assertEqual(
            response.message,
            "이상거래 의심으로 거래가 거절되었습니다.",
        )

    def test_probability_below_threshold_approves_transaction(self) -> None:
        repository = self._repository()
        service = TransactionService(repository)

        prediction = _prediction(result=1, probability=0.49)
        saved_prediction, response = service.save_transaction(
            _transaction(),
            prediction,
        )
        transaction = repository.save_transaction.call_args.args[0]

        self.assertIs(saved_prediction, prediction)
        self.assertEqual(transaction.transaction_status, TransactionStatus.APPROVED)
        self.assertEqual(prediction.transaction_id, 7)
        self.assertEqual(response.prediction_status, "COMPLETED")
        self.assertEqual(response.message, "거래가 승인 되었습니다.")


if __name__ == "__main__":
    unittest.main()
