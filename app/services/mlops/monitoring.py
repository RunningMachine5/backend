"""Cloud Monitoring에서 ML Serving의 운영 지표를 조회한다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
from google import auth as google_auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.core.config import (
    CLOUD_RUN_ADMIN_TIMEOUT_SECONDS,
    CLOUD_RUN_SERVING_SERVICE,
    GCP_PROJECT_ID,
    GCP_REGION,
)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
ALIGNMENT_SECONDS = 60
MONITORING_DATA_DELAY_SECONDS = 120


class CloudMonitoringError(RuntimeError):
    """Cloud Monitoring 인증 또는 시계열 조회 실패."""


class MonitoringAccessTokenProvider:
    """운영 VM의 ADC로 Cloud Monitoring 읽기 토큰을 발급한다."""

    def __init__(self) -> None:
        self._credentials, _ = google_auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        self._request = GoogleAuthRequest()
        self._refresh_lock = Lock()

    def __call__(self) -> str:
        with self._refresh_lock:
            try:
                if not self._credentials.valid:
                    self._credentials.refresh(self._request)
            except GoogleAuthError as exc:
                raise CloudMonitoringError(
                    "Cloud Monitoring용 access token 발급에 실패했습니다."
                ) from exc
            token = self._credentials.token

        if not token:
            raise CloudMonitoringError("Cloud Monitoring access token이 비어 있습니다.")
        return token


@lru_cache
def _monitoring_access_token_provider() -> MonitoringAccessTokenProvider:
    return MonitoringAccessTokenProvider()


class CloudMonitoringClient:
    """Cloud Run ML Serving의 최근 시계열을 1분 단위로 정리한다."""

    def __init__(
        self,
        *,
        project_id: str = GCP_PROJECT_ID,
        region: str = GCP_REGION,
        serving_service: str = CLOUD_RUN_SERVING_SERVICE,
        timeout_seconds: float = CLOUD_RUN_ADMIN_TIMEOUT_SECONDS,
        token_provider: Callable[[], str] | None = None,
        api_base_url: str = "https://monitoring.googleapis.com/v3",
        http_client: httpx.Client | None = None,
    ) -> None:
        if not project_id or not region or not serving_service:
            raise ValueError("Cloud Monitoring 조회 설정이 비어 있습니다.")
        self.project_id = project_id
        self.region = region
        self.serving_service = serving_service
        self.timeout_seconds = timeout_seconds
        self._token_provider = token_provider or _monitoring_access_token_provider()
        self.api_base_url = api_base_url.rstrip("/")
        self._http_client = http_client

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()

    def _query_points(
        self,
        *,
        metric_type: str,
        start: datetime,
        end: datetime,
        aligner: str,
        reducer: str,
        metric_label_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = [
            f'metric.type = "{metric_type}"',
            'resource.type = "cloud_run_revision"',
            f'resource.labels.service_name = "{self.serving_service}"',
            f'resource.labels.location = "{self.region}"',
        ]
        if metric_label_filter:
            filters.append(metric_label_filter)
        params = {
            "filter": " AND ".join(filters),
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.isoformat().replace("+00:00", "Z"),
            "aggregation.alignmentPeriod": f"{ALIGNMENT_SECONDS}s",
            "aggregation.perSeriesAligner": aligner,
            "aggregation.crossSeriesReducer": reducer,
            "view": "FULL",
        }
        try:
            request = self._http_client.get if self._http_client else httpx.get
            response = request(
                f"{self.api_base_url}/projects/{self.project_id}/timeSeries",
                params=params,
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise CloudMonitoringError(
                "Cloud Monitoring 시계열 조회에 실패했습니다."
            ) from exc

        series = response.json().get("timeSeries", [])
        if not series:
            return []
        points = series[0].get("points", [])
        result = []
        for point in points:
            value = point.get("value", {})
            number = value.get("doubleValue", value.get("int64Value"))
            timestamp = point.get("interval", {}).get("endTime")
            if number is not None and timestamp:
                result.append({"timestamp": timestamp, "value": float(number)})
        return sorted(result, key=lambda item: item["timestamp"])

    @staticmethod
    def _latest(points: list[dict[str, Any]]) -> float | None:
        return points[-1]["value"] if points else None

    @staticmethod
    def _percent_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"timestamp": point["timestamp"], "value": point["value"] * 100}
            for point in points
        ]

    @staticmethod
    def _error_rate_points(
        requests: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        errors_by_time = {point["timestamp"]: point["value"] for point in errors}
        return [
            {
                "timestamp": point["timestamp"],
                "value": (
                    errors_by_time.get(point["timestamp"], 0) / point["value"] * 100
                    if point["value"]
                    else 0
                ),
            }
            for point in requests
        ]

    def get_serving_metrics(self, window_minutes: int) -> dict[str, Any]:
        """지정 구간의 Cloud Run 운영 지표와 최신 값을 반환한다."""

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        common = {"start": start, "end": end}
        requests = self._query_points(
            metric_type="run.googleapis.com/request_count",
            aligner="ALIGN_SUM",
            reducer="REDUCE_SUM",
            **common,
        )
        errors = self._query_points(
            metric_type="run.googleapis.com/request_count",
            aligner="ALIGN_SUM",
            reducer="REDUCE_SUM",
            metric_label_filter='metric.labels.response_code_class = "5xx"',
            **common,
        )
        latency = self._query_points(
            metric_type="run.googleapis.com/request_latencies",
            aligner="ALIGN_PERCENTILE_95",
            reducer="REDUCE_PERCENTILE_95",
            **common,
        )
        active_instances = self._query_points(
            metric_type="run.googleapis.com/container/instance_count",
            aligner="ALIGN_MEAN",
            reducer="REDUCE_SUM",
            metric_label_filter='metric.labels.state = "active"',
            **common,
        )
        idle_instances = self._query_points(
            metric_type="run.googleapis.com/container/instance_count",
            aligner="ALIGN_MEAN",
            reducer="REDUCE_SUM",
            metric_label_filter='metric.labels.state = "idle"',
            **common,
        )
        cpu = self._percent_points(
            self._query_points(
                metric_type="run.googleapis.com/container/cpu/utilizations",
                aligner="ALIGN_PERCENTILE_50",
                reducer="REDUCE_MEAN",
                **common,
            )
        )
        memory = self._percent_points(
            self._query_points(
                metric_type="run.googleapis.com/container/memory/utilizations",
                aligner="ALIGN_PERCENTILE_50",
                reducer="REDUCE_MEAN",
                **common,
            )
        )
        error_rates = self._error_rate_points(requests, errors)
        latest_timestamps = [
            points[-1]["timestamp"]
            for points in (requests, latency, active_instances, cpu, memory)
            if points
        ]
        request_count = sum(point["value"] for point in requests)
        error_count = sum(point["value"] for point in errors)

        return {
            "window_minutes": window_minutes,
            "alignment_seconds": ALIGNMENT_SECONDS,
            "data_delay_seconds": MONITORING_DATA_DELAY_SECONDS,
            "service_name": self.serving_service,
            "region": self.region,
            "queried_at": end,
            "latest_sample_at": max(latest_timestamps) if latest_timestamps else None,
            "summary": {
                "request_count": round(request_count),
                "error_rate_percent": (
                    error_count / request_count * 100 if request_count else 0
                ),
                "p95_latency_ms": self._latest(latency),
                "active_instances": self._latest(active_instances),
                "idle_instances": self._latest(idle_instances),
                "cpu_utilization_percent": self._latest(cpu),
                "memory_utilization_percent": self._latest(memory),
            },
            "series": {
                "requests_per_minute": requests,
                "error_rate_percent": error_rates,
                "p95_latency_ms": latency,
                "active_instances": active_instances,
                "cpu_utilization_percent": cpu,
                "memory_utilization_percent": memory,
            },
        }


def get_cloud_monitoring_client(request: Request) -> CloudMonitoringClient:
    return request.app.state.service_clients.monitoring()


CloudMonitoringClientDep = Annotated[
    CloudMonitoringClient,
    Depends(get_cloud_monitoring_client),
]


__all__ = [
    "CloudMonitoringClient",
    "CloudMonitoringClientDep",
    "CloudMonitoringError",
    "get_cloud_monitoring_client",
]
