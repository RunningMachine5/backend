"""관리자 전용 ML 학습·Serving 배포 API."""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
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


class LabeledDatasetBuildRequest(BaseModel):
    """기존 불변 CSV에 DB 확정 라벨을 반영할 새 데이터셋 계약."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_dataset_version_id: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=64)
    gcs_uri: str = Field(min_length=1, max_length=2048)

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
        "created_at": dataset.created_at,
    }


def _training_run_payload(run: TrainingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "model_key": run.model_key,
        "dataset_version_id": run.dataset_version_id,
        "cloud_run_execution_name": run.cloud_run_execution_name,
        "mlflow_run_id": run.mlflow_run_id,
        "status": run.status,
        "created_at": run.created_at,
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

    try:
        # dataset_versions.split_datetime과 training_runs.model_version이
        # ERD에서 빠지면서 학습/평가 분리 시점과 champion 비교 대상을 더는
        # 전달할 수 없다. Training Job이 자체 기본값을 쓰게 된다.
        operation = client.run_training(
            min_pr_auc=payload.min_pr_auc,
            min_recall=payload.min_recall,
            dataset_uri=dataset.gcs_uri,
            training_run_id=run.id,
        )
    except CloudRunAdminError as exc:
        run.status = "FAILED"
        session.add(run)
        session.commit()
        raise _upstream_error(exc) from exc
    run.cloud_run_execution_name = operation.get("name")
    run.status = "RUNNING"
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
    try:
        result = client.promote_model_revision(
            model_version=payload.model_version,
            transaction_id=payload.transaction_id,
            features=payload.features.model_dump(mode="json", by_alias=True),
        )
    except (CloudRunAdminError, MLServingError) as exc:
        raise _upstream_error(exc) from exc
    if run is not None:
        # serving_revision / serving_operation_name이 ERD에서 빠져 배포
        # 진행 상태를 DB에 남기지 못한다. 상태만 갱신한다.
        run.status = "PROMOTING"
        session.add(run)
        session.commit()
    operation = result["operation"]
    return {
        "operation_id": _operation_id(operation),
        **result,
    }


__all__ = ["router"]
