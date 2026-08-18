import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from app.data.model.ml_prediction_result import MLPredictionResult
from app.dto.transaction import TransactionResponseDTO
from app.services.ml_serving.client import MLPredictionResponse
from app.services.transaction.detection_result_service import DetectionResultService


def _prediction(probability: float) -> MLPredictionResponse:
    return MLPredictionResponse(
        transaction_id=7,
        predict_result=int(probability >= 0.5),
        predict_proba=probability,
        shap_values={},
        model_name="fdshield-fraud-detector",
        model_version="3",
    )


class DetectionResultServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        self.transaction = Mock(id=7, created_at=datetime.now(UTC))
        self.session.exec.return_value.one.return_value = self.transaction
        self.features = Mock()
        self.service = DetectionResultService(self.session)

    def _saved_prediction(self) -> MLPredictionResult:
        return next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], MLPredictionResult)
        )

    @patch(
        "app.services.transaction.detection_result_service."
        "score_transaction_fraud_types"
    )
    def test_completed_transaction_saves_ml_result_only(self, score: Mock) -> None:
        response = TransactionResponseDTO(
            transaction_id=7,
            prediction_status="COMPLETED",
            predict_proba=0.1,
            message="거래가 승인 되었습니다.",
        )

        result = self.service.save(
            transaction_response=response,
            prediction=_prediction(0.1),
            features=self.features,
            latency_ms=12,
        )

        score.assert_not_called()
        self.assertFalse(self._saved_prediction().predict_result)
        self.assertFalse(result.response.predict_result)
        self.assertIsNone(result.score_result)

    @patch(
        "app.services.transaction.detection_result_service."
        "score_transaction_fraud_types"
    )
    def test_declined_transaction_saves_rule_scores(self, score: Mock) -> None:
        score_result = Mock(
            rule_set_id=1,
            type_scores={"VOICE_PHISHING": 0.6},
        )
        score.return_value = score_result
        response = TransactionResponseDTO(
            transaction_id=7,
            prediction_status="DECLINED",
            predict_proba=0.9,
            message="이상거래 의심으로 거래가 거절되었습니다.",
        )

        result = self.service.save(
            transaction_response=response,
            prediction=_prediction(0.9),
            features=self.features,
            latency_ms=15,
        )

        score.assert_called_once_with(
            session=self.session,
            transaction_id=7,
            features=self.features,
        )
        self.assertTrue(self._saved_prediction().predict_result)
        self.assertEqual(result.response.rule_set_id, 1)
        self.assertEqual(
            result.response.rule_scores,
            {"VOICE_PHISHING": 0.6},
        )


if __name__ == "__main__":
    unittest.main()
