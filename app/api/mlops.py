"""관리자 전용 ML 학습·Serving 배포 API."""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.core import config
from app.dto.ml_prediction import MLTransactionFeatures
from app.services.ml_serving.client import MLServingError
from app.services.mlops.cloud_run import (
    CloudRunAdminClientDep,
    CloudRunAdminError,
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
    model_config = ConfigDict(extra="forbid")

    auto_promote: bool = True
    min_pr_auc: float = Field(default=0.0, ge=0.0, le=1.0)
    min_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    dataset_uri: str | None = Field(default=None, min_length=1)


class ModelRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(pattern=r"^[0-9]+$")


class ModelPromotionRequest(ModelRevisionRequest):
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


@router.post(
    "/training/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_training_run(
    payload: TrainingRunRequest,
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    """Cloud Run Training Job 실행을 요청하고 operation ID를 반환한다."""

    try:
        operation = client.run_training(
            auto_promote=payload.auto_promote,
            min_pr_auc=payload.min_pr_auc,
            min_recall=payload.min_recall,
            dataset_uri=payload.dataset_uri,
        )
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    return {
        "operation_id": _operation_id(operation),
        "operation": operation,
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
) -> dict[str, Any]:
    """태그 리비전을 실제 예측으로 검증하고 100% 트래픽 승격을 요청한다."""

    try:
        result = client.promote_model_revision(
            model_version=payload.model_version,
            transaction_id=payload.transaction_id,
            features=payload.features.model_dump(mode="json", by_alias=True),
        )
    except (CloudRunAdminError, MLServingError) as exc:
        raise _upstream_error(exc) from exc
    operation = result["operation"]
    return {
        "operation_id": _operation_id(operation),
        **result,
    }


__all__ = ["router"]
