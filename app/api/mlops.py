"""관리자 전용 ML 학습·Serving 배포 API.

전체 흐름은 ``데이터셋 등록 → Training Job 실행 → 결과 callback → 관리자 승인
→ 0% 후보 배포 → 실제 예측 smoke → 100% 전환 → 배포 완료`` 순서다.
Backend DB에는 이 흐름의 최소 상태만 저장하고, 학습 지표와 모델 버전의 원본은
MLflow에서, 실제 리비전과 트래픽의 원본은 Cloud Run에서 다시 확인한다.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.core import config
from app.core.db import SessionDep
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.data.model.transaction import Transaction
from app.dto.mlops import (
    DatasetPeriodRequest,
    DatasetPeriodSummaryResponse,
    DatasetVersionResponse,
    DeploymentCompleteRequest,
    InferencePerformanceResponse,
    LabeledDatasetBuildResponse,
    MLflowDetailsPointer,
    MLflowModelDetails,
    ModelTransactionPageResponse,
    ModelTransactionResponse,
    ModelReviewResponse,
    ModelPromotionRequest,
    ModelUsageSummaryResponse,
    ModelVersionSummaryResponse,
    PlatformMonitoringResponse,
    PlatformStatusResponse,
    ServingMonitoringResponse,
    TrainingDecision,
    TrainingDecisionRequest,
    TrainingExecutionResponse,
    TrainingMonitoringResponse,
    TrainingResultRequest,
    TrainingResultStatus,
    TrainingRunExecutionRequest,
    TrainingRunPrepareRequest,
    TrainingRunResponse,
    TrainingRunStartResponse,
)
from app.repositories.inference_performance import InferencePerformanceRepository
from app.repositories.model_catalog import ModelCatalogRepository, ModelUsageSummary
from app.services.features.ml_feature_assembler import assemble_ml_features
from app.services.ml_serving.client import MLServingError
from app.services.mlops.cloud_run import (
    CloudRunAdminClientDep,
    CloudRunAdminError,
)
from app.services.mlops.dataset_builder import (
    MLOPS_BASE_DATASET_PERIOD_END,
    MLOPS_BASE_DATASET_PERIOD_START,
    MLOPS_BASE_DATASET_URI,
    DatasetBuildError,
    DatasetStorageError,
    LabeledDatasetBuilder,
    LabeledDatasetBuilderDep,
    parse_gcs_uri,
)
from app.services.mlops.mlflow import (
    MLflowRegistryClientDep,
    MLflowRegistryError,
)
from app.services.mlops.model_review import (
    ModelReviewError,
    ModelReviewLLMDep,
)
from app.services.mlops.monitoring import (
    CloudMonitoringClientDep,
    CloudMonitoringError,
)
from app.services.mlops.platform_health import (
    HttpsCertificateError,
    get_https_certificate_expiry,
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


def _latest_verification_sample(session: Session) -> tuple[int, dict[str, Any]]:
    """최근 저장 거래에서 후보 모델 검증용 raw51을 복원한다."""

    source_account = aliased(Account, name="verification_source_account")
    recipient_account = aliased(Account, name="verification_recipient_account")
    row = session.exec(
        select(
            Transaction,
            Customer,
            source_account,
            recipient_account,
            DerivedFeatures,
        )
        .join(Customer, Customer.id == Transaction.customer_id)
        .join(
            source_account,
            source_account.account_number == Transaction.source_account_number,
        )
        .outerjoin(
            recipient_account,
            recipient_account.account_number == Transaction.recipient_account_number,
        )
        .join(DerivedFeatures, DerivedFeatures.id == Transaction.id)
        .order_by(Transaction.transaction_datetime.desc(), Transaction.id.desc())
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="자동 검증에 사용할 저장 거래가 없습니다.",
        )

    transaction, customer, source, recipient, derived = row
    try:
        features = assemble_ml_features(
            customer=customer,
            source_account=source,
            recipient_account=recipient,
            transaction=transaction,
            derived=derived,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail="최근 저장 거래의 raw51 Feature를 복원할 수 없습니다.",
        ) from exc
    return transaction.id, features.model_dump(mode="json", by_alias=True)


def _new_labeled_dataset_target(
    version_number: int,
    period_start: date,
    period_end: date,
    normal_count: int,
    fraud_count: int,
) -> tuple[str, str]:
    """기간과 라벨 수를 이름에 넣어 DB 컬럼 없이도 다시 표시한다."""

    source = parse_gcs_uri(MLOPS_BASE_DATASET_URI)
    created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    period = f"{period_start:%Y%m%d}-{period_end:%Y%m%d}"
    version = (
        f"train_v{version_number}-labeled-{period}"
        f"-n{normal_count}-f{fraud_count}-{created_at}"
    )
    gcs_uri = f"gs://{source.bucket}/{DATASET_VERSION_DIRECTORY}/{version}.csv"
    return version, gcs_uri


def _dataset_metadata_from_version(
    version: str,
) -> tuple[date | None, date | None, int, int]:
    """우리가 만든 버전명에서 기간과 정상·사기 건수를 읽는다."""

    match = re.fullmatch(
        r".+-labeled-(\d{8})-(\d{8})-n(\d+)-f(\d+)-\d{8}T\d{6}Z",
        version,
    )
    if match is None:
        return None, None, 0, 0
    return (
        datetime.strptime(match.group(1), "%Y%m%d").date(),
        datetime.strptime(match.group(2), "%Y%m%d").date(),
        int(match.group(3)),
        int(match.group(4)),
    )


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


def _quality_policy_configured() -> bool:
    return config.MLOPS_MIN_PR_AUC > 0 and config.MLOPS_MIN_RECALL > 0


def _quality_policy_thresholds() -> tuple[float, float]:
    if not _quality_policy_configured():
        return 0.0, 0.0
    return config.MLOPS_MIN_PR_AUC, config.MLOPS_MIN_RECALL


def _model_quality_gate(details: dict[str, Any]) -> dict[str, Any]:
    metrics = details.get("metrics")
    tags = details.get("tags")
    validation_pr_auc = (
        metrics.get("validation_pr_auc") if isinstance(metrics, dict) else None
    )
    validation_recall = (
        metrics.get("validation_recall") if isinstance(metrics, dict) else None
    )
    validation_status = (
        tags.get("validation_status") if isinstance(tags, dict) else None
    )
    minimum_pr_auc, minimum_recall = _quality_policy_thresholds()
    configured = _quality_policy_configured()
    passed = not configured or (
        validation_status == "passed"
        and validation_pr_auc is not None
        and validation_recall is not None
        and validation_pr_auc >= minimum_pr_auc
        and validation_recall >= minimum_recall
    )
    return {
        "configured": configured,
        "minimum_pr_auc": minimum_pr_auc,
        "minimum_recall": minimum_recall,
        "validation_pr_auc": validation_pr_auc,
        "validation_recall": validation_recall,
        "validation_status": validation_status,
        "passed": passed,
    }


def _dataset_payload(dataset: DatasetVersion) -> DatasetVersionResponse:
    assert dataset.id is not None
    period_start, period_end, normal_count, fraud_count = (
        _dataset_metadata_from_version(dataset.version)
    )
    return DatasetVersionResponse(
        id=dataset.id,
        version=dataset.version,
        gcs_uri=dataset.gcs_uri,
        row_count=dataset.row_count,
        period_start=period_start,
        period_end=period_end,
        period_normal_count=normal_count,
        period_fraud_count=fraud_count,
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


def _create_requested_training_run(
    dataset_version_id: int,
    session: SessionDep,
) -> tuple[TrainingRun, DatasetVersion]:
    dataset = session.get(DatasetVersion, dataset_version_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="학습 데이터셋 버전을 찾을 수 없습니다.")

    run = TrainingRun(
        model_key=config.MLOPS_MODEL_NAME,
        dataset_version_id=dataset_version_id,
        status="REQUESTED",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run, dataset


def _execute_training_run(
    run_id: int,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    run = _get_training_run_for_update_or_404(run_id, session)
    if run.status != "REQUESTED":
        raise HTTPException(status_code=409, detail="이미 실행 요청된 학습 Run입니다.")

    dataset = session.get(DatasetVersion, run.dataset_version_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="학습 데이터셋 버전을 찾을 수 없습니다.")

    # 상세 화면이 다시 열려도 같은 Run을 두 번 실행하지 않도록 먼저 상태를 확정한다.
    run.status = "RUNNING"
    session.add(run)
    session.commit()

    try:
        min_pr_auc, min_recall = _quality_policy_thresholds()
        operation = client.run_training(
            min_pr_auc=min_pr_auc,
            min_recall=min_recall,
            dataset_uri=dataset.gcs_uri,
            training_run_id=run.id,
        )
    except CloudRunAdminError as exc:
        locked_run = _get_training_run_for_update_or_404(run_id, session)
        if locked_run.status == "RUNNING" and not exc.request_may_have_been_accepted:
            locked_run.status = "FAILED"
            session.add(locked_run)
            session.commit()
        raise _upstream_error(exc) from exc

    # 매우 빠른 Job callback이 먼저 도착했다면 완료 상태를 다시 RUNNING으로 내리지 않는다.
    locked_run = _get_training_run_for_update_or_404(run_id, session)
    execution_name = client.training_execution_name(operation)
    if locked_run.cloud_run_execution_name is None and execution_name is not None:
        locked_run.cloud_run_execution_name = execution_name
    session.add(locked_run)
    session.commit()
    session.refresh(locked_run)
    return {
        "training_run": _training_run_payload(locked_run),
        "operation_id": _operation_id(operation),
        "operation": operation,
    }


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


def _get_run_model_details(
    run: TrainingRun,
    mlflow: MLflowRegistryClientDep,
) -> dict[str, Any]:
    if run.mlflow_run_id is None:
        raise HTTPException(
            status_code=409,
            detail="학습 실행에 MLflow run ID가 기록되지 않았습니다.",
        )
    try:
        return mlflow.get_model_details(run.model_key, run.mlflow_run_id)
    except MLflowRegistryError as exc:
        raise _upstream_error(exc) from exc


def _model_usage_payload(
    usage: ModelUsageSummary | None,
) -> ModelUsageSummaryResponse:
    if usage is None:
        return ModelUsageSummaryResponse(
            processed_transaction_count=0,
            fraud_prediction_count=0,
            labeled_transaction_count=0,
            matching_label_count=0,
            false_positive_count=0,
            false_negative_count=0,
            label_agreement_percent=None,
            average_latency_ms=None,
            first_inference_at=None,
            latest_inference_at=None,
        )
    agreement = (
        round(usage.matching_label_count / usage.labeled_transaction_count * 100, 1)
        if usage.labeled_transaction_count > 0
        else None
    )
    return ModelUsageSummaryResponse(
        processed_transaction_count=usage.processed_transaction_count,
        fraud_prediction_count=usage.fraud_prediction_count,
        labeled_transaction_count=usage.labeled_transaction_count,
        matching_label_count=usage.matching_label_count,
        false_positive_count=usage.false_positive_count,
        false_negative_count=usage.false_negative_count,
        label_agreement_percent=agreement,
        average_latency_ms=usage.average_latency_ms,
        first_inference_at=usage.first_inference_at,
        latest_inference_at=usage.latest_inference_at,
    )


@router.get("/datasets", response_model=list[DatasetVersionResponse])
def list_dataset_versions(session: SessionDep) -> list[DatasetVersionResponse]:
    datasets = session.exec(
        select(DatasetVersion).order_by(DatasetVersion.created_at.desc())
    ).all()
    return [_dataset_payload(dataset) for dataset in datasets]


@router.post(
    "/datasets/preview",
    response_model=DatasetPeriodSummaryResponse,
)
def preview_labeled_dataset_version(
    payload: DatasetPeriodRequest,
    session: SessionDep,
) -> DatasetPeriodSummaryResponse:
    """선택 기간에 학습 데이터로 추가할 확정 라벨 건수를 보여준다."""

    try:
        summary = LabeledDatasetBuilder.label_summary(
            session,
            period_start=payload.period_start,
            period_end=payload.period_end,
        )
    except DatasetBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DatasetPeriodSummaryResponse(
        base_period_start=MLOPS_BASE_DATASET_PERIOD_START,
        base_period_end=MLOPS_BASE_DATASET_PERIOD_END,
        period_start=payload.period_start,
        period_end=payload.period_end,
        labeled_count=summary.labeled_count,
        normal_count=summary.normal_count,
        fraud_count=summary.fraud_count,
    )


@router.delete(
    "/datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_dataset_version(
    dataset_id: int,
    builder: LabeledDatasetBuilderDep,
    session: SessionDep,
) -> Response:
    """학습에 사용하지 않은 데이터셋을 GCS와 목록에서 삭제한다."""

    dataset = session.get(DatasetVersion, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    linked_run = session.exec(
        select(TrainingRun).where(TrainingRun.dataset_version_id == dataset_id).limit(1)
    ).first()
    if linked_run is not None:
        raise HTTPException(
            status_code=409,
            detail="학습 이력이 연결된 데이터셋은 삭제할 수 없습니다.",
        )
    try:
        builder.delete_dataset(dataset.gcs_uri)
    except DatasetStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DatasetBuildError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    session.delete(dataset)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/datasets/build",
    status_code=status.HTTP_201_CREATED,
    response_model=LabeledDatasetBuildResponse,
)
def build_labeled_dataset_version(
    payload: DatasetPeriodRequest,
    builder: LabeledDatasetBuilderDep,
    session: SessionDep,
) -> dict[str, Any]:
    """고정 GCS CSV와 DB 확정 라벨 거래를 병합해 새 불변 버전을 만든다."""

    try:
        summary = builder.label_summary(
            session,
            period_start=payload.period_start,
            period_end=payload.period_end,
        )
    except DatasetBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if summary.labeled_count == 0:
        raise HTTPException(status_code=422, detail="반영할 확정 거래 라벨이 없습니다.")

    # DB가 부여하는 ID를 v1, v2 버전 번호로 사용한다.
    reservation = secrets.token_hex(8)
    source = parse_gcs_uri(MLOPS_BASE_DATASET_URI)
    dataset = DatasetVersion(
        version=f"pending-{reservation}",
        gcs_uri=(
            f"gs://{source.bucket}/{DATASET_VERSION_DIRECTORY}/"
            f"pending-{reservation}.csv"
        ),
        row_count=0,
    )
    session.add(dataset)
    session.flush()
    assert dataset.id is not None

    version, gcs_uri = _new_labeled_dataset_target(
        dataset.id,
        payload.period_start,
        payload.period_end,
        summary.normal_count,
        summary.fraud_count,
    )
    dataset.version = version
    dataset.gcs_uri = gcs_uri

    try:
        result = builder.build(
            session,
            destination_uri=gcs_uri,
            period_start=payload.period_start,
            period_end=payload.period_end,
        )
    except DatasetStorageError as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DatasetBuildError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dataset.row_count = result.output_row_count
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
            "normal_count": result.normal_count,
            "fraud_count": result.fraud_count,
        },
    }


@router.post(
    "/training/runs/prepare",
    status_code=status.HTTP_201_CREATED,
    response_model=TrainingRunResponse,
)
def prepare_training_run(
    payload: TrainingRunPrepareRequest,
    session: SessionDep,
) -> TrainingRunResponse:
    """상세 화면에서 추적할 REQUESTED Run을 먼저 만든다."""

    run, _ = _create_requested_training_run(payload.dataset_version_id, session)
    return _training_run_payload(run)


@router.post(
    "/training/runs/{run_id}/execute",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TrainingRunStartResponse,
)
def execute_training_run(
    run_id: int,
    _payload: TrainingRunExecutionRequest,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """준비된 Run 하나에 Cloud Run 학습 실행을 연결한다."""

    return _execute_training_run(
        run_id,
        client,
        session,
    )


# Training Job은 Backend 요청과 별도로 실행되므로 성공·실패 결과를 callback으로
# 돌려준다. 아래 조회/결과 API는 그 비동기 실행 상태를 연결하는 경계다.


@router.get("/training/runs", response_model=list[TrainingRunResponse])
def list_training_runs(session: SessionDep) -> list[TrainingRunResponse]:
    runs = session.exec(
        select(TrainingRun).order_by(TrainingRun.created_at.desc())
    ).all()
    return [_training_run_payload(run) for run in runs]


@router.get("/models", response_model=list[ModelVersionSummaryResponse])
def list_model_versions(
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> list[ModelVersionSummaryResponse]:
    """학습이 끝나 MLflow에 등록된 모델과 실제 처리 이력을 함께 반환한다."""

    runs = list(
        session.exec(
            select(TrainingRun)
            .where(TrainingRun.mlflow_run_id.is_not(None))
            .order_by(TrainingRun.created_at.desc(), TrainingRun.id.desc())
        ).all()
    )
    if not runs:
        return []

    versions_by_model: dict[str, dict[str, str]] = {}
    try:
        for model_name in {run.model_key for run in runs}:
            versions_by_model[model_name] = mlflow.model_versions_by_run(model_name)
    except MLflowRegistryError as exc:
        raise _upstream_error(exc) from exc

    dataset_ids = {run.dataset_version_id for run in runs}
    datasets = {
        dataset.id: dataset
        for dataset in session.exec(
            select(DatasetVersion).where(DatasetVersion.id.in_(dataset_ids))
        ).all()
    }
    usage_by_version = ModelCatalogRepository(session).usage_by_model_version()
    current_production_id = next(
        (run.id for run in runs if run.status == "PRODUCTION"),
        None,
    )

    models: list[ModelVersionSummaryResponse] = []
    for run in runs:
        assert run.id is not None
        assert run.mlflow_run_id is not None
        model_version = versions_by_model[run.model_key].get(run.mlflow_run_id)
        dataset = datasets.get(run.dataset_version_id)
        # MLflow에 실제 등록 모델이 없는 학습 이력은 모델 목록이 아니라
        # 학습·배포 이력에서 확인한다.
        if model_version is None or dataset is None:
            continue
        display_status = (
            "RETIRED"
            if run.status == "PRODUCTION" and run.id != current_production_id
            else run.status
        )
        models.append(
            ModelVersionSummaryResponse(
                training_run_id=run.id,
                model_name=run.model_key,
                model_version=model_version,
                status=display_status,
                dataset_version_id=dataset.id,
                dataset_version=dataset.version,
                created_at=run.created_at,
                usage=_model_usage_payload(
                    usage_by_version.get((run.model_key, model_version))
                ),
            )
        )
    return models


@router.get(
    "/models/{run_id}/transactions",
    response_model=ModelTransactionPageResponse,
)
def list_model_transactions(
    run_id: int,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
    label_filter: Literal["ALL", "LABELED", "MISMATCH"] = Query(default="ALL"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> ModelTransactionPageResponse:
    """선택 모델의 최근 처리 거래와 담당자 확정 판정을 조회한다."""

    run = _get_training_run_or_404(run_id, session)
    model_version = _resolve_run_model_version(run, mlflow)
    rows, total_count = ModelCatalogRepository(session).list_transactions(
        model_name=run.model_key,
        model_version=model_version,
        label_filter=label_filter,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return ModelTransactionPageResponse(
        items=[
            ModelTransactionResponse(
                transaction_id=transaction.id,
                transaction_datetime=transaction.transaction_datetime,
                transaction_amount=transaction.transaction_amount,
                channel=transaction.channel,
                predict_result=prediction.predict_result,
                predict_proba=prediction.predict_proba,
                confirmed_is_fraud=(
                    label.confirmed_is_fraud if label is not None else None
                ),
                label_matches=(
                    prediction.predict_result == label.confirmed_is_fraud
                    if label is not None
                    else None
                ),
            )
            for transaction, prediction, label in rows
        ],
        page=page,
        page_size=page_size,
        total_count=total_count,
    )


@router.get("/training/runs/{run_id}", response_model=TrainingRunResponse)
def get_training_run(run_id: int, session: SessionDep) -> TrainingRunResponse:
    return _training_run_payload(_get_training_run_or_404(run_id, session))


@router.get(
    "/training/runs/{run_id}/execution",
    response_model=TrainingExecutionResponse,
)
def get_training_run_execution(
    run_id: int,
    client: CloudRunAdminClientDep,
    session: SessionDep,
) -> TrainingExecutionResponse:
    """학습 Run에 연결된 Cloud Run Execution의 운영 정보만 반환한다."""

    run = _get_training_run_or_404(run_id, session)
    if run.cloud_run_execution_name is None:
        raise HTTPException(
            status_code=409,
            detail="아직 Cloud Run execution이 연결되지 않았습니다.",
        )
    try:
        execution = client.get_training_execution(run.cloud_run_execution_name)
        outcome = client.training_execution_outcome(execution)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc

    terminal = execution.get("terminalCondition")
    failure_reason = None
    if outcome == "FAILED" and isinstance(terminal, dict):
        failure_reason = terminal.get("message") or terminal.get("reason")

    return TrainingExecutionResponse(
        name=execution.get("name", run.cloud_run_execution_name),
        outcome=outcome,
        create_time=execution.get("createTime"),
        start_time=execution.get("startTime"),
        completion_time=execution.get("completionTime"),
        running_count=execution.get("runningCount", 0),
        succeeded_count=execution.get("succeededCount", 0),
        failed_count=execution.get("failedCount", 0),
        cancelled_count=execution.get("cancelledCount", 0),
        retried_count=execution.get("retriedCount", 0),
        log_uri=execution.get("logUri"),
        failure_reason=failure_reason,
    )


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
    details = _get_run_model_details(run, mlflow)
    return {**details, "quality_gate": _model_quality_gate(details)}


@router.post(
    "/training/runs/{run_id}/ai-review",
    response_model=ModelReviewResponse,
)
def review_training_run_with_ai(
    run_id: int,
    mlflow: MLflowRegistryClientDep,
    reviewer: ModelReviewLLMDep,
    session: SessionDep,
) -> ModelReviewResponse:
    """후보와 현재 운영 모델의 성능 수치만 AI에 전달해 검토한다."""

    candidate = _get_training_run_or_404(run_id, session)
    if candidate.status != "CANDIDATE":
        raise HTTPException(
            status_code=409,
            detail="AI 판단은 검토 대기 후보 모델에서만 요청할 수 있습니다.",
        )
    if candidate.mlflow_run_id is None:
        raise HTTPException(
            status_code=409,
            detail="후보 모델의 MLflow run ID가 기록되지 않았습니다.",
        )

    production = session.exec(
        select(TrainingRun)
        .where(TrainingRun.status == "PRODUCTION")
        .order_by(TrainingRun.created_at.desc())
    ).first()

    try:
        candidate_details = mlflow.get_model_details(
            candidate.model_key,
            candidate.mlflow_run_id,
        )
        production_details = (
            mlflow.get_model_details(
                production.model_key,
                production.mlflow_run_id,
            )
            if production is not None and production.mlflow_run_id is not None
            else None
        )
        result = reviewer.review(
            candidate_run_id=run_id,
            candidate_details=candidate_details,
            production_run_id=production.id if production else None,
            production_details=production_details,
        )
    except (MLflowRegistryError, ModelReviewError) as exc:
        raise _upstream_error(exc) from exc

    return ModelReviewResponse(
        decision=result.decision,
        summary=result.summary,
    )


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

    if payload.status == TrainingResultStatus.RUNNING:
        if execution_changed:
            session.add(run)
            session.commit()
            session.refresh(run)
        return _training_run_payload(run)

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
    """학습 실행을 거절하거나 같은 Serving 이미지로 0% 후보를 만든다."""

    run = _get_training_run_for_update_or_404(run_id, session)
    initial_decision = run.status == "CANDIDATE" and not payload.restage
    explicit_restage = (
        run.status == "STAGED"
        and payload.decision == TrainingDecision.APPROVE
        and payload.restage
    )
    if not initial_decision and not explicit_restage:
        raise HTTPException(status_code=409, detail="검토 가능한 후보 모델이 아닙니다.")
    if payload.decision == TrainingDecision.APPROVE and _quality_policy_configured():
        quality_gate = _model_quality_gate(_get_run_model_details(run, mlflow))
        if not quality_gate["passed"]:
            raise HTTPException(
                status_code=409,
                detail="모델 품질 기준을 충족하지 못해 승인할 수 없습니다.",
            )

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
        result = client.stage_model_revision(model_version)
    except CloudRunAdminError as exc:
        raise _upstream_error(exc) from exc
    # Cloud Run이 후보 생성 요청을 받은 뒤에만 관리자 결정을 기록한다.
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


@router.post(
    "/training/runs/{run_id}/reactivate",
    status_code=status.HTTP_202_ACCEPTED,
)
def reactivate_training_run(
    run_id: int,
    client: CloudRunAdminClientDep,
    mlflow: MLflowRegistryClientDep,
    session: SessionDep,
) -> dict[str, Any]:
    """이전에 운영한 모델을 0% 후보로 다시 준비한다."""

    run = _get_training_run_for_update_or_404(run_id, session)
    current_production_id = session.exec(
        select(TrainingRun.id)
        .where(TrainingRun.status == "PRODUCTION")
        .order_by(TrainingRun.created_at.desc(), TrainingRun.id.desc())
    ).first()
    is_previous_production = run.status in {"RETIRED", "PRODUCTION"}
    if not is_previous_production or run.id == current_production_id:
        raise HTTPException(
            status_code=409,
            detail="이전에 운영한 모델만 다시 준비할 수 있습니다.",
        )

    model_version = _resolve_run_model_version(run, mlflow)
    try:
        result = client.stage_model_revision(model_version)
    except CloudRunAdminError as exc:
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


@router.get(
    "/training/monitoring",
    response_model=TrainingMonitoringResponse,
)
def get_training_monitoring(
    client: CloudMonitoringClientDep,
    window_minutes: int = Query(default=60, ge=15, le=1440),
) -> dict[str, Any]:
    """Cloud Run Training Job의 실행 수와 자원 시계열을 반환한다."""

    try:
        return client.get_training_metrics(window_minutes)
    except CloudMonitoringError as exc:
        raise _upstream_error(exc) from exc


@router.get("/platform/status", response_model=PlatformStatusResponse)
def get_platform_status(session: SessionDep) -> PlatformStatusResponse:
    """Backend 응답 여부와 DB 연결 상태를 가볍게 확인한다."""

    started_at = perf_counter()
    try:
        session.exec(text("SELECT 1"))
    except SQLAlchemyError:
        return PlatformStatusResponse(
            database_status="DOWN",
            database_latency_ms=None,
        )
    return PlatformStatusResponse(
        database_status="UP",
        database_latency_ms=round((perf_counter() - started_at) * 1000, 1),
    )


@router.get(
    "/platform/monitoring",
    response_model=PlatformMonitoringResponse,
)
def get_platform_monitoring(
    request: Request,
    session: SessionDep,
    client: CloudMonitoringClientDep,
    window_minutes: int = Query(default=60, ge=15, le=1440),
) -> dict[str, Any]:
    """운영 VM 자원과 실제로 저장된 거래 분석 처리량을 반환한다."""

    try:
        result = client.get_platform_metrics(window_minutes)
    except CloudMonitoringError as exc:
        raise _upstream_error(exc) from exc

    alignment_seconds = result["alignment_seconds"]
    since = datetime.now() - timedelta(minutes=window_minutes)
    try:
        throughput = InferencePerformanceRepository(session).summarize_throughput(
            since,
            alignment_seconds,
        )
    except SQLAlchemyError:
        throughput = None

    mlflow_latency_ms = None
    try:
        mlflow = request.app.state.service_clients.mlflow()
        started_at = perf_counter()
        mlflow.check_registry()
        mlflow_latency_ms = round((perf_counter() - started_at) * 1000, 1)
    except (AttributeError, MLflowRegistryError):
        pass

    certificate_expires_at = None
    certificate_days_remaining = None
    try:
        certificate_expires_at = get_https_certificate_expiry(
            config.PLATFORM_HTTPS_HOST
        )
        certificate_days_remaining = int(
            (certificate_expires_at - datetime.now(UTC)).total_seconds() // 86400
        )
    except HttpsCertificateError:
        pass

    result["summary"].update(
        {
            "analysis_completed_count": (
                throughput.completed_count if throughput else None
            ),
            "normal_analysis_count": throughput.normal_count if throughput else None,
            "fraud_analysis_count": throughput.fraud_count if throughput else None,
        }
    )
    result["series"].update(
        {
            "normal_analysis_count": throughput.normal_series if throughput else [],
            "fraud_analysis_count": throughput.fraud_series if throughput else [],
        }
    )
    result["dependencies"] = {
        "mlflow_latency_ms": mlflow_latency_ms,
        "https_certificate_expires_at": certificate_expires_at,
        "https_certificate_days_remaining": certificate_days_remaining,
    }
    return result


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
    window_minutes: int = Query(default=60, ge=15, le=1440),
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
    if run.status not in {"STAGED", "DEPLOYMENT_FAILED"}:
        raise HTTPException(status_code=409, detail="승격 가능한 학습 실행이 아닙니다.")
    model_version = _resolve_run_model_version(run, mlflow)
    transaction_id, features = _latest_verification_sample(session)

    # 외부 트래픽 변경보다 DB 상태를 먼저 확정한다. 이후 응답이 유실돼도
    # deployment/complete가 실제 Cloud Run 상태를 기준으로 복구할 수 있다.
    run.status = "PROMOTING"
    session.add(run)
    session.commit()
    try:
        result = client.promote_model_revision(
            model_version=model_version,
            transaction_id=transaction_id,
            features=features,
        )
    except (CloudRunAdminError, MLServingError) as exc:
        request_may_have_been_accepted = (
            isinstance(exc, CloudRunAdminError)
            and exc.request_may_have_been_accepted
        )
        if not request_may_have_been_accepted:
            failed_run = _get_training_run_for_update_or_404(
                payload.training_run_id,
                session,
            )
            if failed_run.status == "PROMOTING":
                failed_run.status = "DEPLOYMENT_FAILED"
                session.add(failed_run)
                session.commit()
        raise _upstream_error(exc) from exc
    session.refresh(run)
    operation = result["operation"]
    return {
        "training_run": _training_run_payload(run),
        "model_version": model_version,
        "verification_transaction_id": transaction_id,
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
    if run.status not in {"STAGED", "PROMOTING", "DEPLOYMENT_FAILED", "PRODUCTION"}:
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
    previous_production_runs = session.exec(
        select(TrainingRun).where(
            TrainingRun.status == "PRODUCTION",
            TrainingRun.id != run.id,
        )
    ).all()
    for previous_run in previous_production_runs:
        previous_run.status = "RETIRED"
        session.add(previous_run)
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
