"""관리자 전용 ML 학습·Serving 배포 API.

전체 흐름은 ``데이터셋 등록 → Training Job 실행 → 결과 callback → 관리자 승인
→ 0% 후보 배포 → 실제 예측 smoke → 100% 전환 → 배포 완료`` 순서다.
Backend DB에는 이 흐름의 최소 상태만 저장하고, 학습 지표와 모델 버전의 원본은
MLflow에서, 실제 리비전과 트래픽의 원본은 Cloud Run에서 다시 확인한다.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core import config
from app.core.db import SessionDep
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.dto.mlops import (
    CloudRunOperationResponse,
    DatasetVersionRequest,
    DatasetVersionResponse,
    DeploymentCompleteRequest,
    InferencePerformanceResponse,
    LabeledDatasetBuildResponse,
    MLflowDetailsPointer,
    MLflowModelDetails,
    ModelPromotionRequest,
    ServingMonitoringResponse,
    TrainingDecision,
    TrainingDecisionRequest,
    TrainingResultRequest,
    TrainingResultStatus,
    TrainingRunRequest,
    TrainingRunResponse,
    TrainingRunStartResponse,
)
from app.repositories.inference_performance import InferencePerformanceRepository
from app.services.ml_serving.client import MLServingError
from app.services.mlops.cloud_run import (
    CloudRunAdminClientDep,
    CloudRunAdminError,
)
from app.services.mlops.dataset_builder import (
    MLOPS_BASE_DATASET_URI,
    DatasetBuildError,
    DatasetStorageError,
    LabeledDatasetBuilderDep,
    parse_gcs_uri,
)
from app.services.mlops.mlflow import (
    MLflowRegistryClientDep,
    MLflowRegistryError,
)
from app.services.mlops.monitoring import (
    CloudMonitoringClientDep,
    CloudMonitoringError,
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

PERFORMANCE_WINDOW_MINUTES = 5
DATASET_VERSION_DIRECTORY = "versions"

# DatasetVersion은 CSV 자체를 DB에 복사하지 않고, 학습에 사용할 불변 GCS
# 객체의 주소와 버전만 가리킨다.


def _new_labeled_dataset_target() -> tuple[str, str]:
    """고정 원본 이름과 생성 시각으로 새 버전명과 저장 위치를 만든다."""

    source = parse_gcs_uri(MLOPS_BASE_DATASET_URI)
    source_name = PurePosixPath(source.name).stem
    created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    version = f"{source_name}-labeled-{created_at}"
    gcs_uri = f"gs://{source.bucket}/{DATASET_VERSION_DIRECTORY}/{version}.csv"
    return version, gcs_uri


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


def _dataset_payload(dataset: DatasetVersion) -> DatasetVersionResponse:
    assert dataset.id is not None
    return DatasetVersionResponse(
        id=dataset.id,
        version=dataset.version,
        gcs_uri=dataset.gcs_uri,
        row_count=dataset.row_count,
        created_at=dataset.created_at,
    )


def _training_run_payload(run: TrainingRun) -> TrainingRunResponse:
    assert run.id is not None
    details_endpoint = (
        f"/mlops/training/runs/{run.id}/model-details"
        if run.mlflow_run_id is not None
        else None
    )
    return TrainingRunResponse(
        id=run.id,
        model_key=run.model_key,
        dataset_version_id=run.dataset_version_id,
        cloud_run_execution_name=run.cloud_run_execution_name,
        mlflow_run_id=run.mlflow_run_id,
        status=run.status,
        error_message=run.error_message,
        created_at=run.created_at,
        model_details=MLflowDetailsPointer(
            run_id=run.mlflow_run_id,
            details_endpoint=details_endpoint,
        ),
    )


def _get_training_run_or_404(run_id: int, session: SessionDep) -> TrainingRun:
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="학습 실행 이력을 찾을 수 없습니다.")
    return run


def _get_training_run_for_update_or_404(
    run_id: int,
    session: SessionDep,
) -> TrainingRun:
    """상태전이 직전에 최신 행을 다시 읽고 PostgreSQL row lock을 잡는다."""

    run = session.exec(
        select(TrainingRun)
        .where(TrainingRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="학습 실행 이력을 찾을 수 없습니다.")
    return run


def _resolve_run_model_version(
    run: TrainingRun,
    mlflow: MLflowRegistryClientDep,
) -> str:
    if run.mlflow_run_id is None:
        raise HTTPException(
            status_code=409,
            detail="학습 실행에 MLflow run ID가 기록되지 않았습니다.",
        )
    try:
        return mlflow.resolve_model_version(run.model_key, run.mlflow_run_id)
    except MLflowRegistryError as exc:
        raise _upstream_error(exc) from exc


@router.post(
    "/datasets",
    status_code=status.HTTP_201_CREATED,
    response_model=DatasetVersionResponse,
)
def create_dataset_version(
    payload: DatasetVersionRequest,
    session: SessionDep,
) -> DatasetVersionResponse:
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


@router.get("/datasets", response_model=list[DatasetVersionResponse])
def list_dataset_versions(session: SessionDep) -> list[DatasetVersionResponse]:
    datasets = session.exec(
        select(DatasetVersion).order_by(DatasetVersion.created_at.desc())
    ).all()
    return [_dataset_payload(dataset) for dataset in datasets]


@router.post(
    "/datasets/build",
    status_code=status.HTTP_201_CREATED,
    response_model=LabeledDatasetBuildResponse,
)
def build_labeled_dataset_version(
    builder: LabeledDatasetBuilderDep,
    session: SessionDep,
) -> dict[str, Any]:
    """고정 GCS CSV와 DB 확정 라벨 거래를 병합해 새 불변 버전을 만든다."""

    version, gcs_uri = _new_labeled_dataset_target()
    existing_version = session.exec(
        select(DatasetVersion).where(DatasetVersion.version == version)
    ).first()
    if existing_version is not None:
        raise HTTPException(
            status_code=409,
            detail="이미 존재하는 데이터셋 버전입니다.",
        )

    try:
        result = builder.build(
            session,
            destination_uri=gcs_uri,
        )
    except DatasetStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DatasetBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dataset = DatasetVersion(
        version=version,
        gcs_uri=gcs_uri,
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
        **_dataset_payload(dataset).model_dump(),
        "base_dataset_uri": MLOPS_BASE_DATASET_URI,
        "build": {
            "source_row_count": result.source_row_count,
            "confirmed_label_count": result.confirmed_label_count,
            "appended_label_count": result.appended_label_count,
        },
    }


@router.post(
    "/training/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TrainingRunStartResponse,
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
        # 데이터셋 분리 정책과 champion 버전은 Training Job/MLflow가 원본이다.
        # Backend는 고정된 데이터 URI와 실행 ID만 override한다.
        operation = client.run_training(
            min_pr_auc=payload.min_pr_auc,
            min_recall=payload.min_recall,
            dataset_uri=dataset.gcs_uri,
            training_run_id=run.id,
        )
    except CloudRunAdminError as exc:
        # jobs.run 응답이 늦거나 끊긴 사이 Job callback이 먼저 종결했을 수
        # 있다. timeout/5xx/응답 파싱 실패는 요청 수락 여부가 불명하므로
        # REQUESTED를 유지해 뒤늦은 callback과 reconcile을 허용한다. 명시적인
        # 4xx 같은 확정 거절만 FAILED로 종결한다.
        locked_run = _get_training_run_for_update_or_404(run.id, session)
        if (
            locked_run.status == "REQUESTED"
            and not exc.request_may_have_been_accepted
        ):
            locked_run.status = "FAILED"
            session.add(locked_run)
            session.commit()
        raise _upstream_error(exc) from exc

    # 매우 빠른 Job은 jobs.run HTTP 응답보다 callback이 먼저 도착할 수 있다.
    # row lock으로 callback과 직렬화하고 REQUESTED 상태만 RUNNING으로 옮긴다.
    locked_run = _get_training_run_for_update_or_404(run.id, session)
    execution_name = client.training_execution_name(operation)
    if locked_run.cloud_run_execution_name is None and execution_name is not None:
        locked_run.cloud_run_execution_name = execution_name
    if locked_run.status == "REQUESTED":
        locked_run.status = "RUNNING"
    session.add(locked_run)
    session.commit()
    session.refresh(locked_run)
    return {
        "training_run": _training_run_payload(locked_run),
        "operation_id": _operation_id(operation),
        "operation": operation,
    }


# Training Job은 Backend 요청과 별도로 실행되므로 성공·실패 결과를 callback으로
# 돌려준다. 아래 조회/결과 API는 그 비동기 실행 상태를 연결하는 경계다.


@router.get("/training/runs", response_model=list[TrainingRunResponse])
def list_training_runs(session: SessionDep) -> list[TrainingRunResponse]:
    runs = session.exec(
        select(TrainingRun).order_by(TrainingRun.created_at.desc())
    ).all()
    return [_training_run_payload(run) for run in runs]


@router.get("/training/runs/{run_id}", response_model=TrainingRunResponse)
def get_training_run(run_id: int, session: SessionDep) -> TrainingRunResponse:
    return _training_run_payload(_get_training_run_or_404(run_id, session))


@router.get(
    "/training/runs/{run_id}/model-details",
    response_model=MLflowModelDetails,
)
def get_training_run_model_details(
    run_id: int,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """지표·모델 버전을 DB 복제본이 아닌 MLflow 원본에서 조회한다."""

    run = _get_training_run_or_404(run_id, session)
    if run.mlflow_run_id is None:
        raise HTTPException(
            status_code=409,
            detail="학습 실행에 MLflow run ID가 기록되지 않았습니다.",
        )
    try:
        return mlflow.get_model_details(run.model_key, run.mlflow_run_id)
    except MLflowRegistryError as exc:
        raise _upstream_error(exc) from exc


@router.post(
    "/training/runs/{run_id}/result",
    response_model=TrainingRunResponse,
)
def record_training_result(
    run_id: int,
    payload: TrainingResultRequest,
    session: SessionDep,
) -> TrainingRunResponse:
    """Training Job 결과를 최소 TrainingRun 스키마에 멱등 기록한다."""

    run = _get_training_run_for_update_or_404(run_id, session)
    execution_changed = False
    if payload.cloud_run_execution_name is not None:
        if (
            run.cloud_run_execution_name is not None
            and run.cloud_run_execution_name != payload.cloud_run_execution_name
        ):
            raise HTTPException(
                status_code=409,
                detail="이미 기록된 Cloud Run execution과 다른 학습 결과입니다.",
            )
        if run.cloud_run_execution_name is None:
            run.cloud_run_execution_name = payload.cloud_run_execution_name
            execution_changed = True

    if payload.status == TrainingResultStatus.SUCCEEDED:
        assert payload.mlflow_run_id is not None
        if run.status == "FAILED":
            raise HTTPException(
                status_code=409,
                detail="실패로 종결된 학습 실행에는 성공 결과를 기록할 수 없습니다.",
            )
        if run.status not in {"REQUESTED", "RUNNING"} and (
            run.mlflow_run_id != payload.mlflow_run_id
        ):
            raise HTTPException(
                status_code=409,
                detail="이미 기록된 MLflow run ID와 다른 학습 결과입니다.",
            )
        if run.status in {"REQUESTED", "RUNNING"} and (
            run.mlflow_run_id is not None
            and run.mlflow_run_id != payload.mlflow_run_id
        ):
            raise HTTPException(
                status_code=409,
                detail="이미 기록된 MLflow run ID와 다른 학습 결과입니다.",
            )
        if run.status in {"REQUESTED", "RUNNING"}:
            run.mlflow_run_id = payload.mlflow_run_id
            run.status = "CANDIDATE"
            execution_changed = True
        if run.error_message is not None:
            run.error_message = None
            execution_changed = True
        if execution_changed:
            session.add(run)
            session.commit()
            session.refresh(run)
        # 같은 성공 callback은 CANDIDATE 이후의 승인/배포 상태를 되돌리지 않는다.
        return _training_run_payload(run)

    if run.status in {"REQUESTED", "RUNNING"}:
        run.status = "FAILED"
        run.error_message = payload.error_message
        execution_changed = True
    elif run.status != "FAILED":
        raise HTTPException(
            status_code=409,
            detail="이미 성공 결과가 기록된 학습 실행을 실패로 변경할 수 없습니다.",
        )
    elif run.error_message is None and payload.error_message is not None:
        run.error_message = payload.error_message
        execution_changed = True
    if execution_changed:
        session.add(run)
        session.commit()
        session.refresh(run)
    return _training_run_payload(run)


@router.post(
    "/training/runs/{run_id}/decision",
    status_code=status.HTTP_202_ACCEPTED,
)
def decide_training_run(
    run_id: int,
    payload: TrainingDecisionRequest,
    client: CloudRunAdminClientDep,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """학습 실행을 거절하거나 MLflow가 확인한 후보를 0%로 staging한다."""

    run = _get_training_run_for_update_or_404(run_id, session)
    initial_decision = run.status == "CANDIDATE" and not payload.restage
    explicit_restage = (
        run.status == "STAGED"
        and payload.decision == TrainingDecision.APPROVE
        and payload.restage
    )
    if not initial_decision and not explicit_restage:
        raise HTTPException(status_code=409, detail="검토 가능한 후보 모델이 아닙니다.")
    model_version = _resolve_run_model_version(run, mlflow)
    decision_tags = {"backend_decision": payload.decision.value}
    if payload.reason:
        decision_tags["backend_decision_reason"] = payload.reason
    if payload.decision == TrainingDecision.REJECT:
        try:
            mlflow.set_model_version_tags(
                run.model_key,
                model_version,
                decision_tags,
            )
        except MLflowRegistryError as exc:
            raise _upstream_error(exc) from exc
        run.status = "REJECTED"
        session.add(run)
        session.commit()
        session.refresh(run)
        return {"training_run": _training_run_payload(run), "operation": None}

    try:
        result = client.verify_staged_model_revision(model_version)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    # 승인 태그는 CD 후보 리비전 검증이 성공한 뒤에만 기록한다. 그렇지 않으면
    # MLflow는 승인됐지만 Backend는 CANDIDATE인 분리 상태가 남는다.
    try:
        mlflow.set_model_version_tags(
            run.model_key,
            model_version,
            decision_tags,
        )
    except MLflowRegistryError as exc:
        raise _upstream_error(exc) from exc
    run.status = "STAGED"
    session.add(run)
    session.commit()
    session.refresh(run)
    operation = result.get("operation")
    operation_id = _operation_id(operation) if isinstance(operation, dict) else None
    return {
        "training_run": _training_run_payload(run),
        "model_version": model_version,
        "operation_id": operation_id,
        **result,
    }


@router.post("/training/runs/{run_id}/reconcile")
def reconcile_training_run(
    run_id: int,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """callback 유실 시 Cloud Run Execution의 종결 실패를 DB 상태에 반영한다.

    성공 Execution만으로는 MLflow run ID를 알 수 없으므로 CANDIDATE를
    추측하지 않는다. 성공했지만 callback이 없는 경우에는 운영자가 callback
    설정을 확인할 수 있도록 409로 명확히 알린다.
    """

    run = _get_training_run_for_update_or_404(run_id, session)
    if run.status not in {"REQUESTED", "RUNNING"}:
        return {
            "training_run": _training_run_payload(run),
            "execution_outcome": "UNCHANGED",
            "execution": None,
        }
    if run.cloud_run_execution_name is None:
        raise HTTPException(
            status_code=409,
            detail="Cloud Run execution 이름이 없어 상태를 대조할 수 없습니다.",
        )
    try:
        execution = client.get_training_execution(run.cloud_run_execution_name)
        outcome = client.training_execution_outcome(execution)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc

    if outcome == "FAILED":
        run.status = "FAILED"
        session.add(run)
        session.commit()
        session.refresh(run)
    elif outcome == "SUCCEEDED" and run.mlflow_run_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "학습 Execution은 성공했지만 결과 callback이 기록되지 않았습니다. "
                "callback 설정과 MLflow run을 확인해야 합니다."
            ),
        )
    return {
        "training_run": _training_run_payload(run),
        "execution_outcome": outcome,
        "execution": execution,
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


@router.get(
    "/operations/{operation_id}",
    response_model=CloudRunOperationResponse,
)
def get_operation(
    operation_id: str,
    client: CloudRunAdminClientDep,
) -> dict[str, Any]:
    try:
        return client.get_operation(operation_id)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc


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


@router.get(
    "/serving/performance",
    response_model=InferencePerformanceResponse,
)
def get_serving_performance(
    session: SessionDep,
) -> InferencePerformanceResponse:
    """최근 5분 동안 저장된 온라인 추론 성능을 반환한다."""

    since = datetime.now() - timedelta(minutes=PERFORMANCE_WINDOW_MINUTES)
    summary = InferencePerformanceRepository(session).summarize_since(since)
    return InferencePerformanceResponse(
        window_minutes=PERFORMANCE_WINDOW_MINUTES,
        inference_count=summary.inference_count,
        p95_latency_ms=summary.p95_latency_ms,
        latest_inference_at=summary.latest_inference_at,
    )


@router.get(
    "/serving/monitoring",
    response_model=ServingMonitoringResponse,
)
def get_serving_monitoring(
    client: CloudMonitoringClientDep,
    window_minutes: int = Query(default=60, ge=15, le=360),
) -> dict[str, Any]:
    """Cloud Run Serving의 최근 인프라 시계열을 반환한다."""

    try:
        return client.get_serving_metrics(window_minutes)
    except CloudMonitoringError as exc:
        raise _upstream_error(exc) from exc


@router.post(
    "/serving/promotions",
    status_code=status.HTTP_202_ACCEPTED,
)
def promote_serving_revision(
    payload: ModelPromotionRequest,
    client: CloudRunAdminClientDep,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """태그 리비전을 실제 예측으로 검증하고 100% 트래픽 승격을 요청한다."""

    run = _get_training_run_for_update_or_404(payload.training_run_id, session)
    if run.status not in {"STAGED", "PROMOTING", "DEPLOYMENT_FAILED"}:
        raise HTTPException(status_code=409, detail="승격 가능한 학습 실행이 아닙니다.")
    model_version = _resolve_run_model_version(run, mlflow)
    try:
        result = client.promote_model_revision(
            model_version=model_version,
            transaction_id=payload.transaction_id,
            features=payload.features.model_dump(mode="json", by_alias=True),
        )
    except (CloudRunAdminError, MLServingError) as exc:
        raise _upstream_error(exc) from exc
    run.status = "PROMOTING"
    session.add(run)
    session.commit()
    session.refresh(run)
    operation = result["operation"]
    return {
        "training_run": _training_run_payload(run),
        "model_version": model_version,
        "operation_id": _operation_id(operation),
        **result,
    }


@router.post("/training/runs/{run_id}/deployment/complete")
def complete_model_deployment(
    run_id: int,
    payload: DeploymentCompleteRequest,
    client: CloudRunAdminClientDep,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Cloud Run live traffic를 확인하고 champion alias와 상태를 종결한다."""

    run = _get_training_run_for_update_or_404(run_id, session)
    if run.status not in {"PROMOTING", "PRODUCTION"}:
        raise HTTPException(status_code=409, detail="완료 확인 대상 배포가 아닙니다.")
    model_version = _resolve_run_model_version(run, mlflow)
    operation: dict[str, Any] | None = None
    if payload.operation_id is not None:
        try:
            operation = client.get_operation(payload.operation_id)
        except CloudRunAdminError as exc:
            raise _upstream_error(exc) from exc
        if not operation.get("done", False):
            raise HTTPException(
                status_code=409,
                detail="트래픽 전환이 아직 진행 중입니다.",
            )
        if operation.get("error"):
            if run.status != "PRODUCTION":
                run.status = "DEPLOYMENT_FAILED"
                session.add(run)
                session.commit()
                session.refresh(run)
            raise HTTPException(
                status_code=502,
                detail="Cloud Run 트래픽 전환에 실패했습니다.",
            )
    try:
        deployment = client.get_model_deployment_status(model_version)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    if not deployment["ready"]:
        raise HTTPException(
            status_code=409,
            detail=deployment["reason"] or "Cloud Run 배포가 아직 완료되지 않았습니다.",
        )
    if run.status != "PRODUCTION":
        try:
            mlflow.set_model_alias(run.model_key, config.MLOPS_MODEL_ALIAS, model_version)
        except MLflowRegistryError as exc:
            raise _upstream_error(exc) from exc
        run.status = "PRODUCTION"
        session.add(run)
        session.commit()
        session.refresh(run)
    return {
        "training_run": _training_run_payload(run),
        "model_version": model_version,
        "operation": operation,
        "deployment": deployment,
    }


__all__ = ["router"]
