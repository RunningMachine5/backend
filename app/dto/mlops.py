"""최신 ERD 기준 MLOps 관리자 API 계약."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dto.ml_prediction import MLTransactionFeatures


class StrictMLOpsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrainingRunRequest(StrictMLOpsDTO):
    dataset_version_id: int = Field(gt=0)
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


class LabeledDatasetBuildRequest(StrictMLOpsDTO):
    base_dataset_version_id: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=64)
    gcs_uri: str = Field(min_length=1, max_length=2048)

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
        return _validated_gcs_uri(value)


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
    """Training Job callback.

    ``model_version``과 ``comparison_result``는 배포 중인 구형 Job의 재시도를
    깨지 않기 위한 호환 입력일 뿐이며 Backend DB에는 저장하지 않습니다.
    모델 상세와 지표의 원본은 MLflow입니다.
    """

    status: TrainingResultStatus
    mlflow_run_id: str | None = Field(default=None, min_length=1, max_length=255)
    cloud_run_execution_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    error_message: str | None = Field(default=None, max_length=2000)
    model_version: str | None = Field(default=None, pattern=r"^[0-9]+$")
    comparison_result: dict[str, Any] | None = None

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
    # STAGED 후보를 ML Serving CD가 다시 준비한 뒤 계약 검증을 명시적으로
    # 반복할 때만 사용합니다. 일반 HTTP 재시도로 상태를 다시 쓰지 않도록
    # 기본값은 false입니다.
    restage: bool = False

    @model_validator(mode="after")
    def validate_restage(self) -> Self:
        if self.restage and self.decision != TrainingDecision.APPROVE:
            raise ValueError("restage는 APPROVE 결정에만 사용할 수 있습니다.")
        return self


class ModelPromotionRequest(StrictMLOpsDTO):
    training_run_id: int = Field(gt=0)
    transaction_id: str = Field(min_length=1, max_length=64)
    features: MLTransactionFeatures


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
    "DatasetVersionRequest",
    "DeploymentCompleteRequest",
    "LabeledDatasetBuildRequest",
    "MLflowDetailsPointer",
    "MLflowModelDetails",
    "ModelPromotionRequest",
    "TrainingDecision",
    "TrainingDecisionRequest",
    "TrainingResultRequest",
    "TrainingResultStatus",
    "TrainingRunRequest",
]
