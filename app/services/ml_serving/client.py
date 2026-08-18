"""Backend에서 ML Serving의 HTTP 계약을 호출하는 클라이언트."""

import logging
from collections.abc import Callable
from functools import lru_cache
from threading import Lock
from time import sleep
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, Request
from google.auth import compute_engine
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel, Field, ValidationError

from app.core.config import (
    ML_SERVING_AUTH_MODE,
    ML_SERVING_MAX_ATTEMPTS,
    ML_SERVING_RETRY_DELAY_SECONDS,
    ML_SERVING_TIMEOUT_SECONDS,
    ML_SERVING_URL,
)

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class MLPredictionResponse(BaseModel):
    """ML 담당자의 정식 ``/ml/predict`` 응답."""

    # 거래 ID는 ML 입력이 아니라 Backend 저장 후 붙이는 DB 식별자다.
    transaction_id: int | None = Field(default=None, strict=True, gt=0)
    predict_result: Literal[0, 1]
    predict_proba: float = Field(ge=0.0, le=1.0)
    shap_values: dict[str, float] = Field(default_factory=dict)
    model_name: str
    model_version: str

    @property
    def is_fraud(self) -> bool:
        return bool(self.predict_result)

    @property
    def fraud_probability(self) -> float:
        return self.predict_proba

    @property
    def shap(self) -> dict[str, float]:
        return self.shap_values


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
    """Backend와 ML Serving 사이의 정식 동기 ``/ml/predict`` 호출."""

    def __init__(
        self,
        base_url: str = ML_SERVING_URL,
        timeout_seconds: float = ML_SERVING_TIMEOUT_SECONDS,
        auth_mode: str = ML_SERVING_AUTH_MODE,
        token_provider: Callable[[], str] | None = None,
        max_attempts: int = ML_SERVING_MAX_ATTEMPTS,
        retry_delay_seconds: float = ML_SERVING_RETRY_DELAY_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.auth_mode = auth_mode.strip().lower()
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._http_client = http_client

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

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in RETRYABLE_STATUS_CODES
        return isinstance(
            exc,
            (httpx.TimeoutException, httpx.NetworkError, MLServingError),
        )

    def to_ml(self, features: dict[str, Any]) -> MLPredictionResponse:
        """doo Pipeline에서 사용하는 ML 요청 진입점."""

        return self.predict(features=features)

    def predict(
        self,
        *,
        features: dict[str, Any],
        transaction_id: int | None = None,
    ) -> MLPredictionResponse:
        for attempt in range(1, self.max_attempts + 1):
            try:
                post = self._http_client.post if self._http_client else httpx.post
                response = post(
                    f"{self.base_url}/ml/predict",
                    json=features,
                    headers=self._authorization_headers(),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                prediction = MLPredictionResponse.model_validate(response.json())
            except (
                httpx.HTTPError,
                ValidationError,
                ValueError,
                MLServingError,
            ) as exc:
                if attempt < self.max_attempts and self._is_retryable(exc):
                    if self.retry_delay_seconds:
                        sleep(self.retry_delay_seconds)
                    continue
                logger.warning(
                    "ML Serving 호출 실패: transaction_id=%s attempts=%s error=%s",
                    transaction_id,
                    attempt,
                    type(exc).__name__,
                )
                raise MLServingError("ML 추론 서버 호출에 실패했습니다.") from exc

            prediction.transaction_id = transaction_id
            return prediction

        raise AssertionError("ML Serving 재시도 루프가 결과 없이 종료되었습니다.")

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()


def get_ml_serving_client(request: Request) -> MLServingClient:
    """애플리케이션 lifespan이 소유한 공용 추론 클라이언트를 제공한다."""

    return request.app.state.service_clients.ml_serving()


MLServingClientDep = Annotated[MLServingClient, Depends(get_ml_serving_client)]
