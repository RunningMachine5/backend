from app.services.transaction.transaction_service import TransactionService
from app.dto.transaction import TransactionRequestDTO
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.predict_client import MLServingClient

class DFraudDetectionPipeline:
    def __init__(self, derived_features_service: DerivedFeatureService, ml_serving_client: MLServingClient, transaction_service: TransactionService):
        self.derived_features_service = derived_features_service
        self.ml_serving_client = ml_serving_client
        self.transaction_service = transaction_service

    async def run(self, transaction: TransactionRequestDTO):
        ml_request_features, tx = self.derived_features_service.create_derived_features(transaction)
        predict_result = await self.ml_serving_client.to_ml(ml_request_features.model_dump())
        preres_with_id = self.transaction_service.create_transaction(tx, predict_result)

        print(preres_with_id)
        return None





