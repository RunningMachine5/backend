"""현재 ML Stub의 HTTP 계약을 호출하는 최소 클라이언트."""

from typing import Annotated, Any

import httpx
from fastapi import Depends
from pydantic import BaseModel, Field, ValidationError

from app.core.config import ML_SERVING_TIMEOUT_SECONDS, ML_SERVING_URL


class MLPredictionResponse(BaseModel):
    """ML Stub이 반환하는 이진 분류 응답."""

    transaction_id: str
    is_fraud: bool
    fraud_probability: float = Field(ge=0.0, le=1.0)
    shap: dict[str, float] = Field(default_factory=dict)
    model_name: str
    model_version: str


class MLServingError(RuntimeError):
    """ML 서버 호출 또는 응답 검증에 실패한 경우."""


class MLServingClient:
    """Backend와 ML Stub 사이의 동기 `/predict` 호출만 담당한다."""

    def __init__(
        self,
        base_url: str = ML_SERVING_URL,
        timeout_seconds: float = ML_SERVING_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, Any],
    ) -> MLPredictionResponse:
        try:
            response = httpx.post(
                f"{self.base_url}/predict",
                json={"transaction_id": transaction_id, "features": features},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            prediction = MLPredictionResponse.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise MLServingError("ML 추론 서버 호출에 실패했습니다.") from exc

        if prediction.transaction_id != transaction_id:
            raise MLServingError("ML 응답의 transaction_id가 요청과 다릅니다.")
        return prediction


def get_ml_serving_client() -> MLServingClient:
    """테스트에서 대체할 수 있도록 ML 클라이언트를 의존성으로 제공한다."""

    return MLServingClient()


MLServingClientDep = Annotated[MLServingClient, Depends(get_ml_serving_client)]
