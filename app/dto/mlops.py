"""최신 ERD 기준 MLOps 관리자 API 계약."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictMLOpsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrainingRunRequest(StrictMLOpsDTO):
    dataset_version_id: int = Field(gt=0)
    min_pr_auc: float = Field(default=0.0, ge=0.0, le=1.0)
    min_recall: float = Field(default=0.0, ge=0.0, le=1.0)


class TrainingRunPrepareRequest(StrictMLOpsDTO):
    dataset_version_id: int = Field(gt=0)


class TrainingRunExecutionRequest(StrictMLOpsDTO):
    min_pr_auc: float = Field(default=0.0, ge=0.0, le=1.0)
    min_recall: float = Field(default=0.0, ge=0.0, le=1.0)


class DatasetVersionRequest(StrictMLOpsDTO):
    version: str = Field(min_length=1, max_length=64)
    gcs_uri: str = Field(min_length=1, max_length=2048)
    row_count: int = Field(ge=0)

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
        return _validated_gcs_uri(value)


class DatasetPeriodRequest(StrictMLOpsDTO):
    """기본 데이터에 추가할 판정 완료 거래 기간."""

    period_start: date
    period_end: date

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.period_start > self.period_end:
            raise ValueError("기간 시작일은 종료일보다 늦을 수 없습니다.")
        return self


def _validated_gcs_uri(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or parsed.path in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("학습 데이터 URI는 gs://bucket/object 형식이어야 합니다.")
    return value


class TrainingResultStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class TrainingResultRequest(StrictMLOpsDTO):
    """Training Job이 Backend에 기록하는 최소 callback 계약."""

    status: TrainingResultStatus
    mlflow_run_id: str | None = Field(default=None, min_length=1, max_length=255)
    cloud_run_execution_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.status == TrainingResultStatus.SUCCEEDED and self.mlflow_run_id is None:
            raise ValueError("성공한 학습 결과에는 mlflow_run_id가 필요합니다.")
        return self


class TrainingDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class TrainingDecisionRequest(StrictMLOpsDTO):
    decision: TrainingDecision
    # 확정 ERD에는 사유 컬럼이 없습니다. 요청 감사 로그에서 활용할 수 있도록
    # 호환은 유지하지만 영속 데이터로 취급하지 않습니다.
    reason: str | None = Field(default=None, max_length=2000)
    # 이미 STAGED인 후보를 다시 확인할 때만 사용합니다. 일반 HTTP 재시도로
    # 상태를 다시 쓰지 않도록 기본값은 false입니다.
    restage: bool = False

    @model_validator(mode="after")
    def validate_restage(self) -> Self:
        if self.restage and self.decision != TrainingDecision.APPROVE:
            raise ValueError("restage는 APPROVE 결정에만 사용할 수 있습니다.")
        return self


class ModelPromotionRequest(StrictMLOpsDTO):
    training_run_id: int = Field(gt=0)


class DeploymentCompleteRequest(StrictMLOpsDTO):
    """필요하면 DB에 저장하지 않은 Cloud Run operation을 함께 검증합니다."""

    operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class MLflowDetailsPointer(StrictMLOpsDTO):
    source: Literal["MLFLOW"] = "MLFLOW"
    run_id: str | None
    details_endpoint: str | None


class DatasetVersionResponse(StrictMLOpsDTO):
    """프론트에서 선택할 수 있는 불변 학습 데이터셋 버전."""

    id: int
    version: str
    gcs_uri: str
    row_count: int
    period_start: date | None
    period_end: date | None
    period_normal_count: int
    period_fraud_count: int
    created_at: datetime


class DatasetPeriodSummaryResponse(StrictMLOpsDTO):
    base_period_start: date
    base_period_end: date
    period_start: date
    period_end: date
    labeled_count: int
    normal_count: int
    fraud_count: int


class DatasetBuildSummaryResponse(StrictMLOpsDTO):
    source_row_count: int
    confirmed_label_count: int
    appended_label_count: int
    normal_count: int
    fraud_count: int


class LabeledDatasetBuildResponse(DatasetVersionResponse):
    base_dataset_uri: str
    build: DatasetBuildSummaryResponse


class TrainingRunResponse(StrictMLOpsDTO):
    """DB 상태와 MLflow 상세 조회 위치를 함께 제공하는 학습 실행 응답."""

    id: int
    model_key: str
    dataset_version_id: int
    cloud_run_execution_name: str | None
    mlflow_run_id: str | None
    status: str
    error_message: str | None
    created_at: datetime
    model_details: MLflowDetailsPointer


class InferencePerformanceResponse(StrictMLOpsDTO):
    """모델 관리 화면에 표시할 최근 온라인 추론 성능."""

    window_minutes: int
    inference_count: int
    p95_latency_ms: int | None
    latest_inference_at: datetime | None


class MonitoringPointResponse(StrictMLOpsDTO):
    timestamp: datetime
    value: float


class ServingMonitoringSummaryResponse(StrictMLOpsDTO):
    request_count: int
    error_rate_percent: float
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    pending_p95_latency_ms: float | None
    active_instances: float | None
    idle_instances: float | None
    cpu_utilization_percent: float | None
    memory_utilization_percent: float | None


class ServingMonitoringSeriesResponse(StrictMLOpsDTO):
    request_count: list[MonitoringPointResponse]
    error_rate_percent: list[MonitoringPointResponse]
    p95_latency_ms: list[MonitoringPointResponse]
    p99_latency_ms: list[MonitoringPointResponse]
    pending_p95_latency_ms: list[MonitoringPointResponse]
    active_instances: list[MonitoringPointResponse]
    cpu_utilization_percent: list[MonitoringPointResponse]
    memory_utilization_percent: list[MonitoringPointResponse]


class ServingMonitoringResponse(StrictMLOpsDTO):
    """Cloud Monitoring에서 조회한 Cloud Run 준실시간 운영 지표."""

    window_minutes: int
    alignment_seconds: int
    data_delay_seconds: int
    service_name: str
    region: str
    queried_at: datetime
    latest_sample_at: datetime | None
    summary: ServingMonitoringSummaryResponse
    series: ServingMonitoringSeriesResponse


class TrainingMonitoringSummaryResponse(StrictMLOpsDTO):
    running_executions: float | None
    completed_executions: int
    cpu_utilization_percent: float | None
    memory_utilization_percent: float | None
    billable_instance_seconds: float


class TrainingMonitoringSeriesResponse(StrictMLOpsDTO):
    running_executions: list[MonitoringPointResponse]
    completed_executions: list[MonitoringPointResponse]
    cpu_utilization_percent: list[MonitoringPointResponse]
    memory_utilization_percent: list[MonitoringPointResponse]
    billable_instance_seconds: list[MonitoringPointResponse]


class TrainingMonitoringResponse(StrictMLOpsDTO):
    """Cloud Monitoring에서 조회한 Cloud Run Training Job 지표."""

    window_minutes: int
    alignment_seconds: int
    data_delay_seconds: int
    job_name: str
    region: str
    queried_at: datetime
    latest_sample_at: datetime | None
    summary: TrainingMonitoringSummaryResponse
    series: TrainingMonitoringSeriesResponse


class PlatformMonitoringSummaryResponse(StrictMLOpsDTO):
    cpu_utilization_percent: float | None
    memory_utilization_percent: float | None
    disk_utilization_percent: float | None


class PlatformMonitoringSeriesResponse(StrictMLOpsDTO):
    cpu_utilization_percent: list[MonitoringPointResponse]
    memory_utilization_percent: list[MonitoringPointResponse]
    disk_utilization_percent: list[MonitoringPointResponse]


class PlatformMonitoringResponse(StrictMLOpsDTO):
    """Backend가 실행 중인 Compute Engine VM의 운영 지표."""

    window_minutes: int
    alignment_seconds: int
    data_delay_seconds: int
    instance_id: str
    instance_name: str
    zone: str
    queried_at: datetime
    latest_sample_at: datetime | None
    ops_agent_available: bool
    summary: PlatformMonitoringSummaryResponse
    series: PlatformMonitoringSeriesResponse


class PlatformStatusResponse(StrictMLOpsDTO):
    backend_status: Literal["UP"] = "UP"
    database_status: Literal["UP", "DOWN"]
    database_latency_ms: float | None


class TrainingExecutionResponse(StrictMLOpsDTO):
    """학습 Run과 연결된 Cloud Run Execution의 핵심 상태."""

    name: str
    outcome: Literal["RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]
    create_time: datetime | None
    start_time: datetime | None
    completion_time: datetime | None
    running_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int
    retried_count: int
    log_uri: str | None
    failure_reason: str | None


class CloudRunOperationResponse(BaseModel):
    """Cloud Run 장기 실행 operation의 공통 필드와 확장 필드."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    done: bool | None = None
    error: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class TrainingRunStartResponse(StrictMLOpsDTO):
    training_run: TrainingRunResponse
    operation_id: str | None
    operation: CloudRunOperationResponse


class MLflowModelDetails(StrictMLOpsDTO):
    source: Literal["MLFLOW"] = "MLFLOW"
    run_id: str
    model_name: str
    model_version: str
    artifact_uri: str | None = None
    model_comparison_artifact_path: Literal["metadata/model-comparison.json"] = (
        "metadata/model-comparison.json"
    )
    metrics: dict[str, float]
    params: dict[str, str]
    tags: dict[str, str]


__all__ = [
    "CloudRunOperationResponse",
    "DatasetBuildSummaryResponse",
    "DatasetPeriodRequest",
    "DatasetPeriodSummaryResponse",
    "DatasetVersionRequest",
    "DatasetVersionResponse",
    "DeploymentCompleteRequest",
    "InferencePerformanceResponse",
    "LabeledDatasetBuildResponse",
    "MLflowDetailsPointer",
    "MLflowModelDetails",
    "ModelPromotionRequest",
    "MonitoringPointResponse",
    "PlatformMonitoringResponse",
    "PlatformMonitoringSeriesResponse",
    "PlatformMonitoringSummaryResponse",
    "PlatformStatusResponse",
    "ServingMonitoringResponse",
    "ServingMonitoringSeriesResponse",
    "ServingMonitoringSummaryResponse",
    "TrainingDecision",
    "TrainingDecisionRequest",
    "TrainingResultRequest",
    "TrainingResultStatus",
    "TrainingExecutionResponse",
    "TrainingMonitoringResponse",
    "TrainingMonitoringSeriesResponse",
    "TrainingMonitoringSummaryResponse",
    "TrainingRunExecutionRequest",
    "TrainingRunPrepareRequest",
    "TrainingRunRequest",
    "TrainingRunResponse",
    "TrainingRunStartResponse",
]
