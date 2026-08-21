"""Cloud Monitoring에서 모델 운영에 필요한 서버 지표를 조회한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
    CLOUD_RUN_TRAINING_JOB,
    GCP_PROJECT_ID,
    GCP_REGION,
)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CLOUD_RUN_DATA_DELAY_SECONDS = 120
COMPUTE_DATA_DELAY_SECONDS = 240
GCE_METADATA_URL = "http://metadata.google.internal/computeMetadata/v1"


class CloudMonitoringError(RuntimeError):
    """Cloud Monitoring 인증 또는 시계열 조회 실패."""


@dataclass(frozen=True)
class GceInstanceIdentity:
    """현재 Backend가 실행 중인 Compute Engine VM 식별자."""

    instance_id: str
    instance_name: str
    zone: str


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


@lru_cache
def _gce_instance_identity() -> GceInstanceIdentity:
    """운영 VM의 메타데이터는 한 번만 읽어 이후 지표 조회에 재사용한다."""

    headers = {"Metadata-Flavor": "Google"}

    def read(path: str) -> str:
        try:
            response = httpx.get(
                f"{GCE_METADATA_URL}/{path}",
                headers=headers,
                timeout=2,
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise CloudMonitoringError(
                "운영 VM 정보를 확인하지 못했습니다."
            ) from exc
        return response.text.strip()

    zone = read("instance/zone").rsplit("/", 1)[-1]
    return GceInstanceIdentity(
        instance_id=read("instance/id"),
        instance_name=read("instance/name"),
        zone=zone,
    )


def monitoring_alignment_seconds(window_minutes: int) -> int:
    """긴 조회에서도 차트 점 수가 과도하게 늘어나지 않도록 묶는다."""

    if window_minutes <= 60:
        return 60
    if window_minutes <= 360:
        return 300
    return 900


class CloudMonitoringClient:
    """Serving, Training Job, 운영 VM의 시계열을 같은 형식으로 조회한다."""

    def __init__(
        self,
        *,
        project_id: str = GCP_PROJECT_ID,
        region: str = GCP_REGION,
        serving_service: str = CLOUD_RUN_SERVING_SERVICE,
        training_job: str = CLOUD_RUN_TRAINING_JOB,
        timeout_seconds: float = CLOUD_RUN_ADMIN_TIMEOUT_SECONDS,
        token_provider: Callable[[], str] | None = None,
        instance_identity_provider: Callable[[], GceInstanceIdentity] | None = None,
        api_base_url: str = "https://monitoring.googleapis.com/v3",
        http_client: httpx.Client | None = None,
    ) -> None:
        if not project_id or not region or not serving_service or not training_job:
            raise ValueError("Cloud Monitoring 조회 설정이 비어 있습니다.")
        self.project_id = project_id
        self.region = region
        self.serving_service = serving_service
        self.training_job = training_job
        self.timeout_seconds = timeout_seconds
        self._token_provider = token_provider or _monitoring_access_token_provider()
        self._instance_identity_provider = (
            instance_identity_provider or _gce_instance_identity
        )
        self.api_base_url = api_base_url.rstrip("/")
        self._http_client = http_client

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()

    def _query_points(
        self,
        *,
        metric_type: str,
        resource_type: str,
        resource_labels: dict[str, str],
        start: datetime,
        end: datetime,
        alignment_seconds: int,
        aligner: str,
        reducer: str,
        metric_label_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = [
            f'metric.type = "{metric_type}"',
            f'resource.type = "{resource_type}"',
            *[
                f'resource.labels.{name} = "{value}"'
                for name, value in resource_labels.items()
            ],
        ]
        if metric_label_filter:
            filters.append(metric_label_filter)
        params = {
            "filter": " AND ".join(filters),
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.isoformat().replace("+00:00", "Z"),
            "aggregation.alignmentPeriod": f"{alignment_seconds}s",
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
    def _run_queries_in_parallel(
        queries: Mapping[str, Callable[[], list[dict[str, Any]]]],
    ) -> dict[str, list[dict[str, Any]]]:
        """서로 독립적인 Monitoring 조회를 동시에 실행한다."""

        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            futures = {
                name: executor.submit(query)
                for name, query in queries.items()
            }
            return {
                name: future.result()
                for name, future in futures.items()
            }

    @staticmethod
    def _latest(points: list[dict[str, Any]]) -> float | None:
        return points[-1]["value"] if points else None

    @staticmethod
    def _latest_timestamp(*series: list[dict[str, Any]]) -> str | None:
        timestamps = [points[-1]["timestamp"] for points in series if points]
        return max(timestamps) if timestamps else None

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

    @staticmethod
    def _window(window_minutes: int) -> tuple[datetime, datetime, int]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        return start, end, monitoring_alignment_seconds(window_minutes)

    def get_serving_metrics(self, window_minutes: int) -> dict[str, Any]:
        """Cloud Run Serving의 요청과 응답 자원 지표를 반환한다."""

        start, end, alignment_seconds = self._window(window_minutes)
        common = {
            "resource_type": "cloud_run_revision",
            "resource_labels": {
                "service_name": self.serving_service,
                "location": self.region,
            },
            "start": start,
            "end": end,
            "alignment_seconds": alignment_seconds,
        }
        points = self._run_queries_in_parallel(
            {
                "requests": lambda: self._query_points(
                    metric_type="run.googleapis.com/request_count",
                    aligner="ALIGN_SUM",
                    reducer="REDUCE_SUM",
                    **common,
                ),
                "errors": lambda: self._query_points(
                    metric_type="run.googleapis.com/request_count",
                    aligner="ALIGN_SUM",
                    reducer="REDUCE_SUM",
                    metric_label_filter=(
                        'metric.labels.response_code_class = "5xx"'
                    ),
                    **common,
                ),
                "p95_latency": lambda: self._query_points(
                    metric_type="run.googleapis.com/request_latencies",
                    aligner="ALIGN_PERCENTILE_95",
                    reducer="REDUCE_PERCENTILE_95",
                    **common,
                ),
                "p99_latency": lambda: self._query_points(
                    metric_type="run.googleapis.com/request_latencies",
                    aligner="ALIGN_PERCENTILE_99",
                    reducer="REDUCE_PERCENTILE_99",
                    **common,
                ),
                "pending_latency": lambda: self._query_points(
                    metric_type="run.googleapis.com/request_latency/pending",
                    aligner="ALIGN_PERCENTILE_95",
                    reducer="REDUCE_PERCENTILE_95",
                    **common,
                ),
                "active_instances": lambda: self._query_points(
                    metric_type="run.googleapis.com/container/instance_count",
                    aligner="ALIGN_MEAN",
                    reducer="REDUCE_SUM",
                    metric_label_filter='metric.labels.state = "active"',
                    **common,
                ),
                "idle_instances": lambda: self._query_points(
                    metric_type="run.googleapis.com/container/instance_count",
                    aligner="ALIGN_MEAN",
                    reducer="REDUCE_SUM",
                    metric_label_filter='metric.labels.state = "idle"',
                    **common,
                ),
                "cpu": lambda: self._query_points(
                    metric_type="run.googleapis.com/container/cpu/utilizations",
                    aligner="ALIGN_PERCENTILE_50",
                    reducer="REDUCE_MEAN",
                    **common,
                ),
                "memory": lambda: self._query_points(
                    metric_type=(
                        "run.googleapis.com/container/memory/utilizations"
                    ),
                    aligner="ALIGN_PERCENTILE_50",
                    reducer="REDUCE_MEAN",
                    **common,
                ),
            }
        )
        requests = points["requests"]
        errors = points["errors"]
        p95_latency = points["p95_latency"]
        p99_latency = points["p99_latency"]
        pending_latency = points["pending_latency"]
        active_instances = points["active_instances"]
        idle_instances = points["idle_instances"]
        cpu = self._percent_points(points["cpu"])
        memory = self._percent_points(points["memory"])
        error_rates = self._error_rate_points(requests, errors)
        request_count = sum(point["value"] for point in requests)
        error_count = sum(point["value"] for point in errors)

        return {
            "window_minutes": window_minutes,
            "alignment_seconds": alignment_seconds,
            "data_delay_seconds": CLOUD_RUN_DATA_DELAY_SECONDS,
            "service_name": self.serving_service,
            "region": self.region,
            "queried_at": end,
            "latest_sample_at": self._latest_timestamp(
                requests,
                p95_latency,
                active_instances,
                cpu,
                memory,
            ),
            "summary": {
                "request_count": round(request_count),
                "error_rate_percent": (
                    error_count / request_count * 100 if request_count else 0
                ),
                "p95_latency_ms": self._latest(p95_latency),
                "p99_latency_ms": self._latest(p99_latency),
                "pending_p95_latency_ms": self._latest(pending_latency),
                "active_instances": self._latest(active_instances),
                "idle_instances": self._latest(idle_instances),
                "cpu_utilization_percent": self._latest(cpu),
                "memory_utilization_percent": self._latest(memory),
            },
            "series": {
                "request_count": requests,
                "error_rate_percent": error_rates,
                "p95_latency_ms": p95_latency,
                "p99_latency_ms": p99_latency,
                "pending_p95_latency_ms": pending_latency,
                "active_instances": active_instances,
                "cpu_utilization_percent": cpu,
                "memory_utilization_percent": memory,
            },
        }

    def get_training_metrics(self, window_minutes: int) -> dict[str, Any]:
        """Cloud Run Training Job의 실행 수와 자원 사용량을 반환한다."""

        start, end, alignment_seconds = self._window(window_minutes)
        common = {
            "resource_type": "cloud_run_job",
            "resource_labels": {
                "job_name": self.training_job,
                "location": self.region,
            },
            "start": start,
            "end": end,
            "alignment_seconds": alignment_seconds,
        }
        points = self._run_queries_in_parallel(
            {
                "running": lambda: self._query_points(
                    metric_type="run.googleapis.com/job/running_executions",
                    aligner="ALIGN_MAX",
                    reducer="REDUCE_MAX",
                    **common,
                ),
                "completed": lambda: self._query_points(
                    metric_type=(
                        "run.googleapis.com/job/completed_execution_count"
                    ),
                    aligner="ALIGN_SUM",
                    reducer="REDUCE_SUM",
                    **common,
                ),
                "cpu": lambda: self._query_points(
                    metric_type="run.googleapis.com/container/cpu/utilizations",
                    aligner="ALIGN_PERCENTILE_50",
                    reducer="REDUCE_MEAN",
                    **common,
                ),
                "memory": lambda: self._query_points(
                    metric_type=(
                        "run.googleapis.com/container/memory/utilizations"
                    ),
                    aligner="ALIGN_PERCENTILE_50",
                    reducer="REDUCE_MEAN",
                    **common,
                ),
                "billable_time": lambda: self._query_points(
                    metric_type=(
                        "run.googleapis.com/container/billable_instance_time"
                    ),
                    aligner="ALIGN_SUM",
                    reducer="REDUCE_SUM",
                    **common,
                ),
            }
        )
        running = points["running"]
        completed = points["completed"]
        cpu = self._percent_points(points["cpu"])
        memory = self._percent_points(points["memory"])
        billable_time = points["billable_time"]

        return {
            "window_minutes": window_minutes,
            "alignment_seconds": alignment_seconds,
            "data_delay_seconds": CLOUD_RUN_DATA_DELAY_SECONDS,
            "job_name": self.training_job,
            "region": self.region,
            "queried_at": end,
            "latest_sample_at": self._latest_timestamp(
                running,
                completed,
                cpu,
                memory,
                billable_time,
            ),
            "summary": {
                "running_executions": self._latest(running),
                "completed_executions": round(
                    sum(point["value"] for point in completed)
                ),
                "cpu_utilization_percent": self._latest(cpu),
                "memory_utilization_percent": self._latest(memory),
                "billable_instance_seconds": round(
                    sum(point["value"] for point in billable_time),
                    1,
                ),
            },
            "series": {
                "running_executions": running,
                "completed_executions": completed,
                "cpu_utilization_percent": cpu,
                "memory_utilization_percent": memory,
                "billable_instance_seconds": billable_time,
            },
        }

    def get_platform_metrics(self, window_minutes: int) -> dict[str, Any]:
        """Backend VM의 자원 사용률과 네트워크 처리량을 반환한다."""

        identity = self._instance_identity_provider()
        start, end, alignment_seconds = self._window(window_minutes)
        common = {
            "resource_type": "gce_instance",
            "resource_labels": {
                "instance_id": identity.instance_id,
                "zone": identity.zone,
            },
            "start": start,
            "end": end,
            "alignment_seconds": alignment_seconds,
        }
        points = self._run_queries_in_parallel(
            {
                "cpu": lambda: self._query_points(
                    metric_type=(
                        "compute.googleapis.com/instance/cpu/utilization"
                    ),
                    aligner="ALIGN_MEAN",
                    reducer="REDUCE_MEAN",
                    **common,
                ),
                "memory": lambda: self._query_points(
                    metric_type="agent.googleapis.com/memory/percent_used",
                    aligner="ALIGN_MEAN",
                    reducer="REDUCE_MAX",
                    metric_label_filter='metric.labels.state = "used"',
                    **common,
                ),
                "disk": lambda: self._query_points(
                    metric_type="agent.googleapis.com/disk/percent_used",
                    aligner="ALIGN_MEAN",
                    reducer="REDUCE_MAX",
                    metric_label_filter='metric.labels.state = "used"',
                    **common,
                ),
                "network_received": lambda: self._query_points(
                    metric_type=(
                        "compute.googleapis.com/instance/network/"
                        "received_bytes_count"
                    ),
                    aligner="ALIGN_RATE",
                    reducer="REDUCE_SUM",
                    **common,
                ),
                "network_sent": lambda: self._query_points(
                    metric_type=(
                        "compute.googleapis.com/instance/network/"
                        "sent_bytes_count"
                    ),
                    aligner="ALIGN_RATE",
                    reducer="REDUCE_SUM",
                    **common,
                ),
            }
        )
        cpu = self._percent_points(points["cpu"])
        memory = points["memory"]
        disk = points["disk"]
        network_received = [
            {"timestamp": point["timestamp"], "value": point["value"] / 1024}
            for point in points["network_received"]
        ]
        network_sent = [
            {"timestamp": point["timestamp"], "value": point["value"] / 1024}
            for point in points["network_sent"]
        ]

        return {
            "window_minutes": window_minutes,
            "alignment_seconds": alignment_seconds,
            "data_delay_seconds": COMPUTE_DATA_DELAY_SECONDS,
            "instance_id": identity.instance_id,
            "instance_name": identity.instance_name,
            "zone": identity.zone,
            "queried_at": end,
            "latest_sample_at": self._latest_timestamp(
                cpu,
                memory,
                disk,
                network_received,
                network_sent,
            ),
            "ops_agent_available": bool(memory or disk),
            "summary": {
                "cpu_utilization_percent": self._latest(cpu),
                "memory_utilization_percent": self._latest(memory),
                "disk_utilization_percent": self._latest(disk),
                "network_received_kilobytes_per_second": self._latest(
                    network_received
                ),
                "network_sent_kilobytes_per_second": self._latest(network_sent),
            },
            "series": {
                "cpu_utilization_percent": cpu,
                "memory_utilization_percent": memory,
                "disk_utilization_percent": disk,
                "network_received_kilobytes_per_second": network_received,
                "network_sent_kilobytes_per_second": network_sent,
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
    "GceInstanceIdentity",
    "get_cloud_monitoring_client",
    "monitoring_alignment_seconds",
]
