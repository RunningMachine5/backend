from app.repositories import derived_features
from app.services.transaction.transaction_service import TransactionService
from app.dto.transaction import TransactionRequestDTO, TransactionResponseDTO
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.predict_client import MLServingClient

class DFraudDetectionPipeline:
    def __init__(self, derived_features_service: DerivedFeatureService, ml_serving_client: MLServingClient, transaction_service: TransactionService):
        self.derived_features_service = derived_features_service
        self.ml_serving_client = ml_serving_client
        self.transaction_service = transaction_service

    async def run(self, transaction: TransactionRequestDTO) -> TransactionResponseDTO:
        ml_request_features, tx_data, df_data = self.derived_features_service.create_derived_features(transaction)
        predict_result = await self.ml_serving_client.to_ml(ml_request_features.model_dump(mode="json"))
        result_with_tx_id, tx_response = self.transaction_service.save_transaction(tx_data, predict_result)
        self.derived_features_service.save_derived_features(df_data, tx_response.transaction_id)

        return tx_response

