from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlmodel import select

from app.core.db import SessionDep
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.domain.agent_status import RuleFilterStatus
from app.dto.agent import AgentInputDTO
from app.dto.transaction import (
    TransactionLabelResponseDTO,
    TransactionLabelUpdateDTO,
    TransactionRequestDTO,
    TransactionResponseDTO,
)
from app.pipelines.fraud_detection_pipeline import (
    CustomerIdentificationConflictError,
    FraudDetectionPipeline,
    FraudDetectionResult,
)
from app.repositories.transaction import (
    AccountIdentifierConflictError,
    AccountOwnershipConflictError,
    CustomerReferenceNotFoundError,
    PredictionResultRepository,
    TransactionLabelRepository,
)
from app.services.agent.task_runner import AgentTaskRunnerDep
from app.services.analysis.risk_grader import RiskGrader
from app.services.ml_serving.client import MLServingClientDep

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


def _build_agent_input(
    result: FraudDetectionResult,
) -> AgentInputDTO | None:
    """이상거래의 Rule 결과와 위험등급을 Agent 실행 입력으로 묶는다."""

    prediction = result.prediction_result
    score_result = result.score_result
    transaction_id = result.transaction.id
    if (
        prediction is None
        or not prediction.predict_result
        or score_result is None
        or score_result.rule_filter_status != RuleFilterStatus.APPLIED.value
        or transaction_id is None
        or score_result.id is None
    ):
        return None

    risk = RiskGrader().assess(
        result.transaction.transaction_amount,
        prediction.predict_proba,
    )
    return AgentInputDTO(
        transaction_id=transaction_id,
        fraud_type_score_result_id=score_result.id,
        risk_score=risk.risk_score,
        risk_grade=risk.risk_grade,
    )


def _transaction_response(
    transaction: Transaction,
    prediction_result: MLPredictionResult | None,
    score_result: FraudTypeScoreResult | None,
    label: TransactionLabel | None,
    *,
    prediction_status: str | None = None,
) -> TransactionResponseDTO:
    return TransactionResponseDTO.model_validate(
        {
            # SQLAlchemy가 commit 뒤 객체를 expire하면 SQLModel.model_dump()가
            # 빈 dict를 반환할 수 있다. 응답 계약의 필드를 명시적으로 읽어
            # 세션 상태와 관계없이 같은 응답을 만든다.
            "transaction_id": transaction.id,
            "created_at": transaction.created_at,
            "prediction_status": prediction_status
            or ("COMPLETED" if prediction_result else "NOT_AVAILABLE"),
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
        }
    )


@router.post(
    "",
    response_model=TransactionResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(
    payload: TransactionRequestDTO,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    ml_client: MLServingClientDep,
    agent_task_runner: AgentTaskRunnerDep,
) -> TransactionResponseDTO:
    """HTTP 요청을 실제 사기 탐지 Pipeline에 전달한다."""

    pipeline = FraudDetectionPipeline(session=session, ml_client=ml_client)
    try:
        result = pipeline.run(payload)
    except AccountIdentifierConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="계좌 식별값이 기존 원장과 일치하지 않습니다.",
        ) from exc
    except AccountOwnershipConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 다른 고객이 소유한 출금 계좌입니다.",
        ) from exc
    except CustomerIdentificationConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 다른 고객에 사용 중인 identification_number입니다.",
        ) from exc
    except CustomerReferenceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="고객 원장에서 customer_id를 찾을 수 없습니다.",
        ) from exc

    agent_input = _build_agent_input(result)
    if agent_input is not None:
        background_tasks.add_task(agent_task_runner, agent_input)

    return _transaction_response(
        result.transaction,
        result.prediction_result,
        result.score_result,
        TransactionLabelRepository(session).get(result.transaction.id),
        prediction_status=result.prediction_status,
    )


@router.get("", response_model=list[TransactionResponseDTO])
def list_transactions(session: SessionDep) -> list[TransactionResponseDTO]:
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


@router.get("/{transaction_id}", response_model=TransactionResponseDTO)
def get_transaction(
    transaction_id: int,
    session: SessionDep,
) -> TransactionResponseDTO:
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
