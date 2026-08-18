"""doo 거래 흐름에 운영 결과 저장을 이어 붙인다."""

from time import perf_counter

from app.dto.transaction import TransactionRequestDTO
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClient
from app.services.transaction.detection_result_service import (
    DetectionResultService,
    FraudDetectionResult,
)
from app.services.transaction.transaction_service import TransactionService


class DFraudDetectionPipeline:
    def __init__(
        self,
        derived_features_service: DerivedFeatureService,
        ml_serving_client: MLServingClient,
        transaction_service: TransactionService,
        detection_result_service: DetectionResultService,
    ) -> None:
        self.derived_features_service = derived_features_service
        self.ml_serving_client = ml_serving_client
        self.transaction_service = transaction_service
        self.detection_result_service = detection_result_service

    def run(self, transaction: TransactionRequestDTO) -> FraudDetectionResult:
        ml_request_features, tx_data, df_data = (
            self.derived_features_service.create_derived_features(transaction)
        )
        started_at = perf_counter()
        predict_result = self.ml_serving_client.to_ml(
            ml_request_features.model_dump(mode="json")
        )
        latency_ms = round((perf_counter() - started_at) * 1000)
        result_with_tx_id, tx_response = self.transaction_service.save_transaction(
            tx_data,
            predict_result,
        )
        self.derived_features_service.save_derived_features(
            df_data,
            tx_response.transaction_id,
        )

        # doo 거래 저장 뒤에 MLOps·룰 결과만 이어서 저장한다.
        return self.detection_result_service.save(
            transaction_response=tx_response,
            prediction=result_with_tx_id,
            features=ml_request_features,
            latency_ms=latency_ms,
        )


__all__ = ["DFraudDetectionPipeline", "FraudDetectionResult"]
