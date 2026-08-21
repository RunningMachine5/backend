from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from sqlmodel import select

from app.api.dependencies import DFraudDetectionPipelineDep
from app.core.db import SessionDep
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction, TransactionStatus
from app.data.model.transaction_label import TransactionLabel
from app.dto.fraud_detection import FraudDetectionResponseDTO
from app.dto.transaction import (
    TransactionLabelQueueItemDTO,
    TransactionLabelQueueResponseDTO,
    TransactionLabelQueueSummaryDTO,
    TransactionLabelResponseDTO,
    TransactionLabelStatus,
    TransactionLabelUpdateDTO,
    TransactionPredictionFilter,
    TransactionRequestDTO,
)
from app.repositories.transaction import (
    PredictionResultRepository,
    TransactionLabelRepository,
)
from app.services.agent.task_runner import AgentTaskRunnerDep
from app.services.agent.input_builder import build_agent_input
from app.services.dashboard.dashboard_event_broker import dashboard_event_broker

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


def _transaction_response(
    transaction: Transaction,
    prediction_result: MLPredictionResult | None,
    score_result: FraudTypeScoreResult | None,
    label: TransactionLabel | None,
) -> FraudDetectionResponseDTO:
    """DB에 나뉘어 저장된 거래·ML·룰·라벨을 조회 응답 하나로 합친다."""

    # doo가 저장한 거래 상태가 API의 승인·거절 표시 기준이다.
    if transaction.transaction_status == TransactionStatus.DECLINED:
        prediction_status = "DECLINED"
        message = "이상거래 의심으로 거래가 거절되었습니다."
    else:
        prediction_status = "COMPLETED"
        message = "거래가 승인 되었습니다."

    return FraudDetectionResponseDTO.model_validate(
        {
            # SQLAlchemy가 commit 뒤 객체를 expire하면 SQLModel.model_dump()가
            # 빈 dict를 반환할 수 있다. 응답 계약의 필드를 명시적으로 읽어
            # 세션 상태와 관계없이 같은 응답을 만든다.
            "transaction_id": transaction.id,
            "created_at": transaction.created_at,
            "prediction_status": prediction_status,
            "predict_result": (
                prediction_result.predict_result if prediction_result else None
            ),
            "predict_proba": (
                prediction_result.predict_proba if prediction_result else None
            ),
            "rule_set_id": score_result.rule_set_id if score_result else None,
            "rule_scores": score_result.type_scores if score_result else None,
            "confirmed_is_fraud": (label.confirmed_is_fraud if label else None),
            "labeled_at": label.labeled_at if label else None,
            "message": message,
        }
    )


@router.post(
    "",
    response_model=FraudDetectionResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(
    payload: TransactionRequestDTO,
    background_tasks: BackgroundTasks,
    fraud_detection_pipeline: DFraudDetectionPipelineDep,
    agent_task_runner: AgentTaskRunnerDep,
) -> FraudDetectionResponseDTO:
    """HTTP 요청을 실제 사기 탐지 Pipeline에 전달한다."""

    result = fraud_detection_pipeline.run(payload)
    dashboard_event_broker.publish(
        event="dashboard_updated",
        data={"source": "transaction"},
    )
    agent_input = build_agent_input(result)
    if agent_input is not None:
        background_tasks.add_task(agent_task_runner, agent_input)

    if result.prediction_result is not None and result.prediction_result.predict_result:
        dashboard_event_broker.publish(event="dashboard_updated", data={"source": "ml"})
    # Pipeline이 doo 응답에 ML·룰 결과까지 합쳤으므로 그대로 반환한다.
    return result.response


@router.get("", response_model=list[FraudDetectionResponseDTO])
def list_transactions(session: SessionDep) -> list[FraudDetectionResponseDTO]:
    stmt = select(Transaction).order_by(Transaction.created_at.desc()).limit(20)
    transactions = list(session.exec(stmt).all())
    transaction_ids = [item.id for item in transactions]
    if not transaction_ids:
        return []

    prediction_results = session.exec(
        select(MLPredictionResult)
        .where(MLPredictionResult.transaction_id.in_(transaction_ids))
        .order_by(
            MLPredictionResult.created_at.desc(),
            MLPredictionResult.id.desc(),
        )
    ).all()
    prediction_by_transaction_id: dict[int, MLPredictionResult] = {}
    for item in prediction_results:
        prediction_by_transaction_id.setdefault(item.transaction_id, item)

    score_results = session.exec(
        select(FraudTypeScoreResult).where(
            FraudTypeScoreResult.transaction_id.in_(transaction_ids)
        )
    ).all()
    score_by_transaction_id = {item.transaction_id: item for item in score_results}
    labels = session.exec(
        select(TransactionLabel).where(
            TransactionLabel.transaction_id.in_(transaction_ids)
        )
    ).all()
    label_by_transaction_id = {item.transaction_id: item for item in labels}
    return [
        _transaction_response(
            tx,
            prediction_by_transaction_id.get(tx.id),
            score_by_transaction_id.get(tx.id),
            label_by_transaction_id.get(tx.id),
        )
        for tx in transactions
    ]


@router.get(
    "/label-queue",
    response_model=TransactionLabelQueueResponseDTO,
)
def list_transaction_label_queue(
    session: SessionDep,
    label_status: TransactionLabelStatus = Query(
        default=TransactionLabelStatus.UNLABELED
    ),
    prediction: TransactionPredictionFilter = Query(
        default=TransactionPredictionFilter.ALL
    ),
    transaction_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> TransactionLabelQueueResponseDTO:
    """전체 거래에서 담당자가 확정할 라벨링 대상을 조회한다."""

    repository = TransactionLabelRepository(session)
    rows, total_count = repository.list_for_labeling(
        label_status=label_status.value,
        prediction=prediction.value,
        transaction_id=transaction_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    # 상단 현황은 현재 필터와 무관한 전체 라벨 건수를 보여준다.
    all_count, unlabeled_count, normal_count, fraud_count = repository.summary()

    # Repository의 세 모델을 프론트가 바로 그릴 수 있는 한 행으로 합친다.
    items = [
        TransactionLabelQueueItemDTO(
            transaction_id=transaction.id,
            customer_id=transaction.customer_id,
            transaction_datetime=transaction.transaction_datetime,
            transaction_amount=transaction.transaction_amount,
            channel=transaction.channel,
            transaction_status=transaction.transaction_status,
            source_account_number=transaction.source_account_number,
            recipient_account_number=transaction.recipient_account_number,
            initial_balance=transaction.initial_balance,
            balance=transaction.balance,
            access_medium=transaction.access_medium,
            operating_system=transaction.operating_system,
            ip_address=(
                str(transaction.ip_address)
                if transaction.ip_address is not None
                else None
            ),
            mac_address=(
                str(transaction.mac_address)
                if transaction.mac_address is not None
                else None
            ),
            location_lat=transaction.location_lat,
            location_lon=transaction.location_lon,
            num_connection_failure=transaction.num_connection_failure,
            rooting_jailbreak_indicator=transaction.rooting_jailbreak_indicator,
            mobile_roaming_indicator=transaction.mobile_roaming_indicator,
            vpn_indicator=transaction.vpn_indicator,
            # DB의 세부 악성행위 플래그 5개는 화면에서는 하나의 위험 신호로 표시한다.
            terminal_malicious_behavior_detected=any(
                (
                    transaction.flag_terminal_malicious_behavior_1,
                    transaction.flag_terminal_malicious_behavior_2,
                    transaction.flag_terminal_malicious_behavior_3,
                    transaction.flag_terminal_malicious_behavior_5,
                    transaction.flag_terminal_malicious_behavior_6,
                )
            ),
            predict_result=(prediction_result.predict_result if prediction_result else None),
            predict_proba=(prediction_result.predict_proba if prediction_result else None),
            model_name=(prediction_result.model_name if prediction_result else None),
            model_version=(
                prediction_result.model_version if prediction_result else None
            ),
            predicted_at=(prediction_result.created_at if prediction_result else None),
            confirmed_is_fraud=(label.confirmed_is_fraud if label else None),
            labeled_at=(label.labeled_at if label else None),
        )
        for transaction, prediction_result, label in rows
    ]
    return TransactionLabelQueueResponseDTO(
        items=items,
        summary=TransactionLabelQueueSummaryDTO(
            total_count=all_count,
            unlabeled_count=unlabeled_count,
            normal_count=normal_count,
            fraud_count=fraud_count,
        ),
        page=page,
        page_size=page_size,
        total_count=total_count,
    )


@router.put(
    "/{transaction_id}/label",
    response_model=TransactionLabelResponseDTO,
)
def upsert_transaction_label(
    transaction_id: int,
    payload: TransactionLabelUpdateDTO,
    session: SessionDep,
) -> TransactionLabel:
    """담당자가 확정한 거래 라벨을 생성하거나 변경한다."""

    if session.get(Transaction, transaction_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="거래를 찾을 수 없습니다.",
        )

    label = TransactionLabelRepository(session).upsert(
        transaction_id=transaction_id,
        confirmed_is_fraud=payload.confirmed_is_fraud,
    )
    session.commit()
    session.refresh(label)
    return label


@router.delete(
    "/{transaction_id}/label",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_transaction_label(
    transaction_id: int,
    session: SessionDep,
) -> Response:
    """담당자 판정을 지우고 거래를 미판정 상태로 되돌린다."""

    if session.get(Transaction, transaction_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="거래를 찾을 수 없습니다.",
        )

    # 라벨이 이미 없어도 미판정 상태라는 결과는 같으므로 204로 처리한다.
    TransactionLabelRepository(session).delete(transaction_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{transaction_id}", response_model=FraudDetectionResponseDTO)
def get_transaction(
    transaction_id: int,
    session: SessionDep,
) -> FraudDetectionResponseDTO:
    transaction = session.exec(
        select(Transaction).where(Transaction.id == transaction_id)
    ).first()
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="거래를 찾을 수 없습니다.",
        )
    score_result = session.exec(
        select(FraudTypeScoreResult).where(
            FraudTypeScoreResult.transaction_id == transaction_id
        )
    ).first()
    prediction_result = PredictionResultRepository(session).latest_for_transaction(
        transaction_id
    )
    label = TransactionLabelRepository(session).get(transaction_id)
    return _transaction_response(
        transaction,
        prediction_result,
        score_result,
        label,
    )
