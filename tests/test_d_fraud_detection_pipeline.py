import unittest
from unittest.mock import Mock, patch

from app.data.model.ml_prediction_result import MLPredictionResult
from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.services.ml_serving.client import MLPredictionResponse


def _prediction(*, result: int, probability: float) -> MLPredictionResponse:
    return MLPredictionResponse(
        predict_result=result,
        predict_proba=probability,
        shap_values={},
        model_name="fdshield-fraud-detector",
        model_version="3",
    )


class FraudDetectionPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        self.features = Mock()
        self.features.model_dump.return_value = {"transaction_amount": 10_000}
        self.transaction_data = Mock()
        self.derived_data = Mock()
        self.transaction = Mock(id=7)

        self.derived_service = Mock()
        self.derived_service.create_derived_features.return_value = (
            self.features,
            self.transaction_data,
            self.derived_data,
        )
        self.transaction_service = Mock()
        self.ml_client = Mock()
        self.pipeline = DFraudDetectionPipeline(
            session=self.session,
            derived_features_service=self.derived_service,
            ml_serving_client=self.ml_client,
            transaction_service=self.transaction_service,
        )

    def _saved_prediction(self) -> MLPredictionResult:
        return next(
            call.args[0]
            for call in self.session.add.call_args_list
            if isinstance(call.args[0], MLPredictionResult)
        )

    @patch(
        "app.pipelines.d_fraud_detection_pipline.score_transaction_fraud_types"
    )
    def test_normal_transaction_skips_rule_scoring(self, score: Mock) -> None:
        self.ml_client.predict.return_value = _prediction(result=1, probability=0.1)
        self.transaction_service.save_transaction.return_value = (
            self.transaction,
            False,
        )

        result = self.pipeline.run(Mock())

        self.ml_client.predict.assert_called_once_with(
            features={"transaction_amount": 10_000}
        )
        self.derived_service.save_derived_features.assert_called_once_with(
            self.derived_data,
            7,
        )
        score.assert_not_called()
        self.assertFalse(self._saved_prediction().predict_result)
        self.assertIsNone(result.score_result)
        self.session.commit.assert_called_once_with()

    @patch(
        "app.pipelines.d_fraud_detection_pipline.score_transaction_fraud_types"
    )
    def test_fraud_transaction_saves_rule_result(self, score: Mock) -> None:
        self.ml_client.predict.return_value = _prediction(result=0, probability=0.9)
        self.transaction_service.save_transaction.return_value = (
            self.transaction,
            True,
        )
        score_result = Mock()
        score.return_value = score_result

        result = self.pipeline.run(Mock())

        score.assert_called_once_with(
            session=self.session,
            transaction_id=7,
            features=self.features,
        )
        self.assertTrue(self._saved_prediction().predict_result)
        self.assertIs(result.score_result, score_result)


if __name__ == "__main__":
    unittest.main()
