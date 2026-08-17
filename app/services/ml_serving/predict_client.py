import httpx

from pydantic import BaseModel

class MLPredictionResponse(BaseModel):
    transaction_id: int | None = None
    predict_proba: float
    shap_values: dict

class MLServingClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def to_ml(self, features: dict) -> MLPredictionResponse:
        response = await self.client.post("/ml/predict", json={**features})
        response.raise_for_status()

        return MLPredictionResponse.model_validate(response.json())

    async def aclose(self) -> None:
        await self.client.aclose()

