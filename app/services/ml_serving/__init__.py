"""ML 실시간 추론 서버 연동."""

from app.services.ml_serving.client import (
    MLPredictionResponse,
    MLServingClient,
    MLServingError,
    get_ml_serving_client,
)

__all__ = [
    "MLPredictionResponse",
    "MLServingClient",
    "MLServingError",
    "get_ml_serving_client",
]
