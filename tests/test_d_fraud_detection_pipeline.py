import unittest
from unittest.mock import ANY, Mock

from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.services.ml_serving.client import MLPredictionResponse


class FraudDetectionPipelineTest(unittest.TestCase):
    def test_runs_doo_flow_then_saves_operational_results(self) -> None:
        features = Mock()
        features.model_dump.return_value = {"transaction_amount": 10_000}
        transaction_data = Mock()
        derived_data = Mock()
        transaction_response = Mock(transaction_id=7)
        prediction = MLPredictionResponse(
            predict_result=1,
            predict_proba=0.9,
            shap_values={},
            model_name="fdshield-fraud-detector",
            model_version="3",
        )
        final_result = Mock()

        derived_service = Mock()
        derived_service.create_derived_features.return_value = (
            features,
            transaction_data,
            derived_data,
        )
        ml_client = Mock()
        ml_client.to_ml.return_value = prediction
        transaction_service = Mock()
        transaction_service.save_transaction.return_value = (
            prediction,
            transaction_response,
        )
        result_service = Mock()
        result_service.save.return_value = final_result

        pipeline = DFraudDetectionPipeline(
            derived_features_service=derived_service,
            ml_serving_client=ml_client,
            transaction_service=transaction_service,
            detection_result_service=result_service,
        )

        result = pipeline.run(Mock())

        ml_client.to_ml.assert_called_once_with({"transaction_amount": 10_000})
        transaction_service.save_transaction.assert_called_once_with(
            transaction_data,
            prediction,
        )
        derived_service.save_derived_features.assert_called_once_with(
            derived_data,
            7,
        )
        result_service.save.assert_called_once_with(
            transaction_response=transaction_response,
            prediction=prediction,
            features=features,
            latency_ms=ANY,
        )
        self.assertIs(result, final_result)


if __name__ == "__main__":
    unittest.main()
