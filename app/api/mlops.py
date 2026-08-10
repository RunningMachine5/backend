"""관리자 전용 ML 학습·Serving 배포 API."""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core import config
from app.core.db import SessionDep
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.dto.ml_prediction import MLTransactionFeatures
from app.services.ml_serving.client import MLServingError
from app.services.mlops.cloud_run import (
    CloudRunAdminClientDep,
    CloudRunAdminError,
)
from app.services.mlops.dataset_builder import (
    DatasetBuildError,
    DatasetStorageError,
    LabeledDatasetBuilderDep,
)


def require_mlops_admin(
    token: Annotated[
        str | None,
        Header(alias="X-MLOps-Admin-Token"),
    ] = None,
) -> None:
    """공개 API와 분리된 단일 관리 토큰을 검증한다."""

    expected = config.MLOPS_ADMIN_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MLOps 관리 API가 비활성화되어 있습니다.",
        )
    if token is None or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MLOps 관리 토큰이 올바르지 않습니다.",
        )


router = APIRouter(
    prefix="/mlops",
    tags=["mlops-admin"],
    dependencies=[Depends(require_mlops_admin)],
)


class TrainingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_version_id: int = Field(gt=0)
    min_pr_auc: float = Field(default=0.0, ge=0.0, le=1.0)
    min_recall: float = Field(default=0.0, ge=0.0, le=1.0)


class DatasetVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: str = Field(min_length=1, max_length=64)
    gcs_uri: str = Field(min_length=1, max_length=2048)
    row_count: int = Field(ge=0)
    split_datetime: datetime | None = None

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str | None) -> str | None:
        """Cloud Run Job이 읽을 수 있는 명시적인 GCS 객체만 허용한다."""

        if value is None:
            return None
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

    @field_validator("split_datetime")
    @classmethod
    def validate_split_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("split_datetime에는 시간대를 포함할 수 없습니다.")
        return value


class LabeledDatasetBuildRequest(BaseModel):
    """기존 불변 CSV에 DB 확정 라벨을 반영할 새 데이터셋 계약."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_dataset_version_id: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=64)
    gcs_uri: str = Field(min_length=1, max_length=2048)
    split_datetime: datetime

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
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

    @field_validator("split_datetime")
    @classmethod
    def validate_split_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("split_datetime에는 시간대를 포함할 수 없습니다.")
        return value


class ComparedModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(pattern=r"^[0-9]+$")
    metrics: dict[str, float]


class ModelComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: ComparedModelResult
    production: ComparedModelResult | None = None
    recommendation: str = Field(
        pattern=r"^(RECOMMENDED|REVIEW_REQUIRED|NOT_RECOMMENDED)$"
    )


class TrainingResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(pattern=r"^(SUCCEEDED|FAILED)$")
    mlflow_run_id: str | None = Field(default=None, max_length=255)
    model_version: str | None = Field(default=None, pattern=r"^[0-9]+$")
    comparison_result: ModelComparisonResult | None = None
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.status == "SUCCEEDED" and (
            self.mlflow_run_id is None
            or self.model_version is None
            or self.comparison_result is None
        ):
            raise ValueError("성공한 학습 결과에는 MLflow와 모델 비교 정보가 필요합니다.")
        if self.status == "FAILED" and not self.error_message:
            raise ValueError("실패한 학습 결과에는 error_message가 필요합니다.")
        return self


class TrainingDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class TrainingDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: TrainingDecision
    reason: str | None = Field(default=None, max_length=2000)


class ModelRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(pattern=r"^[0-9]+$")


class ModelPromotionRequest(ModelRevisionRequest):
    training_run_id: int | None = Field(default=None, gt=0)
    transaction_id: str = Field(min_length=1, max_length=64)
    features: MLTransactionFeatures


def _operation_id(payload: dict[str, Any]) -> str | None:
    name = payload.get("name")
    if not isinstance(name, str) or "/operations/" not in name:
        return None
    return name.rsplit("/", 1)[-1]


def _upstream_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


def _dataset_payload(dataset: DatasetVersion) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "version": dataset.version,
        "gcs_uri": dataset.gcs_uri,
        "row_count": dataset.row_count,
        "split_datetime": dataset.split_datetime,
        "created_at": dataset.created_at,
    }


def _training_run_payload(run: TrainingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "model_key": run.model_key,
        "dataset_version_id": run.dataset_version_id,
        "cloud_run_operation_name": run.cloud_run_operation_name,
        "mlflow_run_id": run.mlflow_run_id,
        "model_version": run.model_version,
        "comparison_result": run.comparison_result,
        "decision_reason": run.decision_reason,
        "serving_revision": run.serving_revision,
        "serving_operation_name": run.serving_operation_name,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "decided_at": run.decided_at,
    }


def _get_training_run_or_404(run_id: int, session: SessionDep) -> TrainingRun:
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="학습 실행 이력을 찾을 수 없습니다.")
    return run


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset_version(
    payload: DatasetVersionRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """GCS에 준비된 불변 학습 데이터셋을 버전으로 등록한다."""

    dataset = DatasetVersion(**payload.model_dump())
    session.add(dataset)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="이미 존재하는 데이터셋 버전입니다.") from exc
    session.refresh(dataset)
    return _dataset_payload(dataset)


@router.get("/datasets")
def list_dataset_versions(session: SessionDep) -> list[dict[str, Any]]:
    datasets = session.exec(
        select(DatasetVersion).order_by(DatasetVersion.created_at.desc())
    ).all()
    return [_dataset_payload(dataset) for dataset in datasets]


@router.post("/datasets/build", status_code=status.HTTP_201_CREATED)
def build_labeled_dataset_version(
    payload: LabeledDatasetBuildRequest,
    builder: LabeledDatasetBuilderDep,
    session: SessionDep,
) -> dict[str, Any]:
    """기존 GCS CSV와 DB 확정 라벨 거래를 병합해 새 불변 버전을 만든다."""

    base_dataset = session.get(
        DatasetVersion,
        payload.base_dataset_version_id,
    )
    if base_dataset is None:
        raise HTTPException(
            status_code=404,
            detail="기준 학습 데이터셋 버전을 찾을 수 없습니다.",
        )
    existing_version = session.exec(
        select(DatasetVersion).where(DatasetVersion.version == payload.version)
    ).first()
    if existing_version is not None:
        raise HTTPException(
            status_code=409,
            detail="이미 존재하는 데이터셋 버전입니다.",
        )

    try:
        result = builder.build(
            session,
            source_uri=base_dataset.gcs_uri,
            destination_uri=payload.gcs_uri,
        )
    except DatasetStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DatasetBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dataset = DatasetVersion(
        version=payload.version,
        gcs_uri=payload.gcs_uri,
        row_count=result.output_row_count,
        split_datetime=payload.split_datetime,
    )
    session.add(dataset)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="이미 존재하는 데이터셋 버전입니다.",
        ) from exc
    session.refresh(dataset)
    return {
        **_dataset_payload(dataset),
        "base_dataset_version_id": base_dataset.id,
        "build": {
            "source_row_count": result.source_row_count,
            "confirmed_label_count": result.confirmed_label_count,
            "replaced_label_count": result.replaced_label_count,
            "appended_label_count": result.appended_label_count,
        },
    }


@router.post(
    "/training/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_training_run(
    payload: TrainingRunRequest,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """버전이 고정된 데이터셋으로 학습을 요청하고 실행 이력을 남긴다."""

    dataset = session.get(DatasetVersion, payload.dataset_version_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="학습 데이터셋 버전을 찾을 수 없습니다.")
    run = TrainingRun(
        model_key=config.MLOPS_MODEL_NAME,
        dataset_version_id=payload.dataset_version_id,
        status="REQUESTED",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    champion = session.exec(
        select(TrainingRun)
        .where(
            TrainingRun.model_key == config.MLOPS_MODEL_NAME,
            TrainingRun.status == "PRODUCTION",
        )
        .order_by(TrainingRun.updated_at.desc())
        .limit(1)
    ).first()

    try:
        operation = client.run_training(
            min_pr_auc=payload.min_pr_auc,
            min_recall=payload.min_recall,
            dataset_uri=dataset.gcs_uri,
            split_datetime=(
                dataset.split_datetime.isoformat(sep=" ")
                if dataset.split_datetime is not None
                else None
            ),
            training_run_id=run.id,
            champion_model_version=(champion.model_version if champion else None),
        )
    except CloudRunAdminError as exc:
        run.status = "FAILED"
        run.decision_reason = str(exc)
        run.updated_at = datetime.now()
        session.add(run)
        session.commit()
        raise _upstream_error(exc) from exc
    run.cloud_run_operation_name = operation.get("name")
    run.status = "RUNNING"
    run.updated_at = datetime.now()
    session.add(run)
    session.commit()
    session.refresh(run)
    return {
        "training_run": _training_run_payload(run),
        "operation_id": _operation_id(operation),
        "operation": operation,
    }


@router.get("/training/runs")
def list_training_runs(session: SessionDep) -> list[dict[str, Any]]:
    runs = session.exec(
        select(TrainingRun).order_by(TrainingRun.created_at.desc())
    ).all()
    return [_training_run_payload(run) for run in runs]


@router.get("/training/runs/{run_id}")
def get_training_run(run_id: int, session: SessionDep) -> dict[str, Any]:
    return _training_run_payload(_get_training_run_or_404(run_id, session))


@router.post("/training/runs/{run_id}/result")
def record_training_result(
    run_id: int,
    payload: TrainingResultRequest,
    session: SessionDep,
) -> dict[str, Any]:
    """Training Job이 남긴 후보 모델과 champion 비교 결과를 기록한다."""

    run = _get_training_run_or_404(run_id, session)
    if run.status not in {"REQUESTED", "RUNNING"}:
        raise HTTPException(status_code=409, detail="결과를 기록할 수 없는 학습 상태입니다.")
    run.updated_at = datetime.now()
    if payload.status == "FAILED":
        run.status = "FAILED"
        run.decision_reason = payload.error_message
    else:
        run.status = "CANDIDATE"
        run.mlflow_run_id = payload.mlflow_run_id
        run.model_version = payload.model_version
        run.comparison_result = payload.comparison_result.model_dump()
    session.add(run)
    session.commit()
    session.refresh(run)
    return _training_run_payload(run)


@router.post("/training/runs/{run_id}/decision", status_code=status.HTTP_202_ACCEPTED)
def decide_training_run(
    run_id: int,
    payload: TrainingDecisionRequest,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """관리자 결정에 따라 후보를 거절하거나 0% Serving 리비전으로 올린다."""

    run = _get_training_run_or_404(run_id, session)
    if run.status != "CANDIDATE":
        raise HTTPException(status_code=409, detail="검토 가능한 후보 모델이 아닙니다.")
    run.decision_reason = payload.reason
    run.decided_at = datetime.now()
    run.updated_at = datetime.now()
    if payload.decision == TrainingDecision.REJECT:
        run.status = "REJECTED"
        session.add(run)
        session.commit()
        session.refresh(run)
        return {"training_run": _training_run_payload(run), "operation": None}

    if run.model_version is None:
        raise HTTPException(status_code=409, detail="후보 모델 버전이 비어 있습니다.")
    try:
        result = client.create_model_revision(run.model_version)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    run.status = "STAGED"
    session.add(run)
    session.commit()
    session.refresh(run)
    return {
        "training_run": _training_run_payload(run),
        "operation_id": _operation_id(result["operation"]),
        **result,
    }


@router.get("/training/status")
def get_training_status(
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    try:
        job = client.get_training_status()
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    return {
        "name": job.get("name"),
        "execution_count": job.get("executionCount", 0),
        "latest_execution": job.get("latestCreatedExecution"),
        "reconciling": job.get("reconciling", False),
        "terminal_condition": job.get("terminalCondition"),
    }


@router.get("/operations/{operation_id}")
def get_operation(
    operation_id: str,
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    try:
        return client.get_operation(operation_id)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc


@router.post(
    "/serving/revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_serving_revision(
    payload: ModelRevisionRequest,
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    """현재 트래픽을 보존하고 새 모델의 태그 리비전 생성을 요청한다."""

    try:
        result = client.create_model_revision(payload.model_version)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    operation = result["operation"]
    return {
        "operation_id": _operation_id(operation),
        **result,
    }


@router.get("/serving/status")
def get_serving_status(
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    try:
        service = client.get_serving_status()
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    return {
        "name": service.get("name"),
        "uri": service.get("uri"),
        "reconciling": service.get("reconciling", False),
        "latest_created_revision": service.get("latestCreatedRevision"),
        "latest_ready_revision": service.get("latestReadyRevision"),
        "traffic": service.get("trafficStatuses", []),
        "terminal_condition": service.get("terminalCondition"),
    }


@router.post(
    "/serving/promotions",
    status_code=status.HTTP_202_ACCEPTED,
)
def promote_serving_revision(
    payload: ModelPromotionRequest,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """태그 리비전을 실제 예측으로 검증하고 100% 트래픽 승격을 요청한다."""

    run: TrainingRun | None = None
    if payload.training_run_id is not None:
        run = _get_training_run_or_404(payload.training_run_id, session)
        if run.status != "STAGED" or run.model_version != payload.model_version:
            raise HTTPException(
                status_code=409,
                detail="승격 가능한 학습 실행 또는 모델 버전이 아닙니다.",
            )
    try:
        result = client.promote_model_revision(
            model_version=payload.model_version,
            transaction_id=payload.transaction_id,
            features=payload.features.model_dump(mode="json", by_alias=True),
        )
    except (CloudRunAdminError, MLServingError) as exc:
        raise _upstream_error(exc) from exc
    if run is not None:
        run.status = "PROMOTING"
        run.serving_revision = result.get("revision")
        run.serving_operation_name = result["operation"].get("name")
        run.updated_at = datetime.now()
        session.add(run)
        session.commit()
    operation = result["operation"]
    return {
        "operation_id": _operation_id(operation),
        **result,
    }


@router.post("/training/runs/{run_id}/deployment/complete")
def complete_model_deployment(
    run_id: int,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Cloud Run traffic operation 성공을 확인한 뒤 운영 모델을 확정한다."""

    run = _get_training_run_or_404(run_id, session)
    if run.status != "PROMOTING" or run.serving_operation_name is None:
        raise HTTPException(status_code=409, detail="완료 확인 대상 배포가 아닙니다.")
    operation_id = _operation_id({"name": run.serving_operation_name})
    if operation_id is None:
        raise HTTPException(status_code=409, detail="Serving operation ID가 올바르지 않습니다.")
    try:
        operation = client.get_operation(operation_id)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    if not operation.get("done", False):
        raise HTTPException(status_code=409, detail="트래픽 전환이 아직 진행 중입니다.")
    if operation.get("error"):
        run.status = "DEPLOYMENT_FAILED"
        run.decision_reason = str(operation["error"])
        run.updated_at = datetime.now()
        session.add(run)
        session.commit()
        raise HTTPException(status_code=502, detail="Cloud Run 트래픽 전환에 실패했습니다.")
    run.status = "PRODUCTION"
    run.updated_at = datetime.now()
    session.add(run)
    session.commit()
    session.refresh(run)
    return {"training_run": _training_run_payload(run), "operation": operation}


__all__ = ["router"]
