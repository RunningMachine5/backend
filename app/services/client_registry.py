"""애플리케이션 수명 동안 외부 HTTP 클라이언트를 재사용한다."""

from __future__ import annotations

from threading import Lock

import httpx

from app.services.ml_serving.client import MLServingClient
from app.services.mlops.cloud_run import CloudRunAdminClient
from app.services.mlops.mlflow import MLflowRegistryClient


class ServiceClientRegistry:
    """외부 서비스별 연결 풀을 지연 생성하고 종료 시 함께 정리한다."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._ml_serving: MLServingClient | None = None
        self._mlflow: MLflowRegistryClient | None = None
        self._cloud_run: CloudRunAdminClient | None = None

    def ml_serving(self) -> MLServingClient:
        with self._lock:
            if self._ml_serving is None:
                self._ml_serving = MLServingClient(http_client=httpx.Client())
            return self._ml_serving

    def mlflow(self) -> MLflowRegistryClient:
        with self._lock:
            if self._mlflow is None:
                self._mlflow = MLflowRegistryClient(http_client=httpx.Client())
            return self._mlflow

    def cloud_run(self) -> CloudRunAdminClient:
        with self._lock:
            if self._cloud_run is None:
                self._cloud_run = CloudRunAdminClient(http_client=httpx.Client())
            return self._cloud_run

    def close(self) -> None:
        """실제로 만들어진 클라이언트만 닫는다."""

        with self._lock:
            clients = (self._ml_serving, self._mlflow, self._cloud_run)
            self._ml_serving = None
            self._mlflow = None
            self._cloud_run = None
        for client in clients:
            if client is not None:
                client.close()


__all__ = ["ServiceClientRegistry"]
