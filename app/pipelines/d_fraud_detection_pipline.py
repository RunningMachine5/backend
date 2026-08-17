from app.dto.transaction import TransactionRequestDTO
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.predict_client import MLServingClient


class DFraudDetectionPipeline:
    def __init__(self, derived_features_service: DerivedFeatureService, ml_serving_client: MLServingClient):
        self.derived_features_service = derived_features_service
        self.ml_serving_client = ml_serving_client

    async def run(self, transaction: TransactionRequestDTO):
        ml_request_features = self.derived_features_service.create_derived_features(transaction)
        predict_result = await self.ml_serving_client.to_ml(ml_request_features.model_dump())

        return predict_result