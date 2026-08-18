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
        # 1. doo의 파생 Feature 로직으로 ML 입력, 거래, 저장용 파생값을 만든다.
        ml_request_features, tx_data, df_data = (
            self.derived_features_service.create_derived_features(transaction)
        )

        # 2. 완성된 ML 입력을 추론 서버에 보내고 응답 시간을 함께 기록한다.
        started_at = perf_counter()
        predict_result = self.ml_serving_client.to_ml(
            ml_request_features.model_dump(mode="json")
        )
        latency_ms = round((perf_counter() - started_at) * 1000)

        # 3. doo의 거래 저장 로직이 확률을 보고 승인 또는 거절을 결정한다.
        result_with_tx_id, tx_response = self.transaction_service.save_transaction(
            tx_data,
            predict_result,
        )

        # 4. 거래 ID를 사용해 앞에서 계산한 파생값을 같은 거래에 저장한다.
        self.derived_features_service.save_derived_features(
            df_data,
            tx_response.transaction_id,
        )

        # 5. doo 거래 흐름이 끝난 뒤 우리 MLOps·룰 결과만 이어서 저장한다.
        return self.detection_result_service.save(
            transaction_response=tx_response,
            prediction=result_with_tx_id,
            features=ml_request_features,
            latency_ms=latency_ms,
        )


__all__ = ["DFraudDetectionPipeline", "FraudDetectionResult"]
