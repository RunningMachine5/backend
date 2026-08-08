"""Backend에서 ML Serving의 HTTP 계약을 호출하는 클라이언트."""

from collections.abc import Callable
from functools import lru_cache
from threading import Lock
from typing import Annotated, Any

import httpx
from fastapi import Depends
from google.auth import compute_engine
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel, Field, ValidationError

from app.core.config import (
    ML_SERVING_AUTH_MODE,
    ML_SERVING_TIMEOUT_SECONDS,
    ML_SERVING_URL,
)


class MLPredictionResponse(BaseModel):
    """ML Serving이 반환하는 이진 분류 응답."""

    transaction_id: str
    is_fraud: bool
    fraud_probability: float = Field(ge=0.0, le=1.0)
    shap: dict[str, float] = Field(default_factory=dict)
    model_name: str
    model_version: str


class MLServingError(RuntimeError):
    """ML 서버 호출 또는 응답 검증에 실패한 경우."""


class GoogleIDTokenProvider:
    """GCE 메타데이터에서 Cloud Run용 ID Token을 발급하고 재사용한다."""

    def __init__(self, audience: str) -> None:
        self._request = GoogleAuthRequest()
        self._credentials = compute_engine.IDTokenCredentials(
            request=self._request,
            target_audience=audience,
            use_metadata_identity_endpoint=True,
        )
        self._refresh_lock = Lock()

    def __call__(self) -> str:
        with self._refresh_lock:
            try:
                if not self._credentials.valid:
                    self._credentials.refresh(self._request)
            except GoogleAuthError as exc:
                raise MLServingError(
                    "Cloud Run 호출용 ID Token 발급에 실패했습니다."
                ) from exc

            token = self._credentials.token

        if not token:
            raise MLServingError("Cloud Run 호출용 ID Token이 비어 있습니다.")
        return token


@lru_cache
def _google_id_token_provider(audience: str) -> GoogleIDTokenProvider:
    """Backend 요청 사이에서 유효한 ID Token과 전송 객체를 재사용한다."""

    return GoogleIDTokenProvider(audience)


class MLServingClient:
    """Backend와 ML Serving 사이의 동기 `/predict` 호출을 담당한다."""

    def __init__(
        self,
        base_url: str = ML_SERVING_URL,
        timeout_seconds: float = ML_SERVING_TIMEOUT_SECONDS,
        auth_mode: str = ML_SERVING_AUTH_MODE,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.auth_mode = auth_mode.strip().lower()

        if self.auth_mode == "none":
            self._token_provider = None
        elif self.auth_mode == "google-id-token":
            self._token_provider = token_provider or _google_id_token_provider(
                self.base_url
            )
        else:
            raise ValueError(
                "ML_SERVING_AUTH_MODE must be 'none' or 'google-id-token'."
            )

    def _authorization_headers(self) -> dict[str, str]:
        if self._token_provider is None:
            return {}
        return {"Authorization": f"Bearer {self._token_provider()}"}

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
                headers=self._authorization_headers(),
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
