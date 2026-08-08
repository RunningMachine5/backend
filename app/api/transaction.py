from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, select

from app.core.db import SessionDep
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.transaction import Transaction
from app.dto.ml_prediction import MLTransactionFeatures
from app.services.ml_serving.client import MLServingClientDep, MLServingError
from app.services.rules.scoring import score_transaction_fraud_types

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


class TransactionCreate(SQLModel):
    """전처리 전 ML 원본 Feature 54개를 포함하는 거래 수신 DTO."""

    transaction_id: str = Field(min_length=1, max_length=64)
    occurred_at: datetime | None = None
    raw_data: MLTransactionFeatures


class TransactionResponse(SQLModel):
    """거래·ML 결과와 전체 사기유형 룰 점수를 함께 반환한다."""

    id: int
    transaction_id: str
    occurred_at: datetime
    raw_data: dict[str, object]
    payment_method: str | None
    prediction_status: str
    ml_is_fraud: bool | None
    fraud_probability: float | None
    shap: dict[str, float] | None
    model_name: str | None
    model_version: str | None
    created_at: datetime
    updated_at: datetime
    rule_scores: dict[str, float] | None = None


def _transaction_response(
    transaction: Transaction,
    score_result: FraudTypeScoreResult | None,
) -> TransactionResponse:
    return TransactionResponse.model_validate(
        {
            **transaction.model_dump(),
            "rule_scores": score_result.type_scores if score_result else None,
        }
    )


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(
    payload: TransactionCreate,
    session: SessionDep,
    ml_client: MLServingClientDep,
) -> TransactionResponse:
    """거래 원본을 먼저 저장한 뒤 현재 ML Stub에 동기 추론을 요청한다."""

    # Pydantic이 검증한 날짜와 alias를 JSON에서 사용하는 원본 컬럼명으로 되돌린다.
    # 특히 Python 식별자로 쓸 수 없는 "Time Difference" 키를 그대로 보존한다.
    raw_data = payload.raw_data.model_dump(mode="json", by_alias=True)

    tx = Transaction(
        transaction_id=payload.transaction_id,
        occurred_at=payload.occurred_at or datetime.now(),
        raw_data=raw_data,
    )
    session.add(tx)
    score_result: FraudTypeScoreResult | None = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 존재하는 transaction_id입니다.",
        ) from exc
    session.refresh(tx)

    try:
        prediction = ml_client.predict(
            transaction_id=tx.transaction_id,
            features=raw_data,
        )
    except MLServingError:
        tx.prediction_status = "FAILED"
    else:
        tx.prediction_status = "COMPLETED"
        tx.ml_is_fraud = prediction.is_fraud
        tx.fraud_probability = prediction.fraud_probability
        tx.shap = prediction.shap
        tx.model_name = prediction.model_name
        tx.model_version = prediction.model_version

        if prediction.is_fraud:
            score_result = score_transaction_fraud_types(
                session=session,
                transaction_id=tx.transaction_id,
                raw_data=raw_data,
            )
            if score_result is not None:
                session.add(score_result)

    tx.updated_at = datetime.now()
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return _transaction_response(tx, score_result)


@router.get("", response_model=list[TransactionResponse])
def list_transactions(session: SessionDep) -> list[TransactionResponse]:
    # SELECT * FROM transactions ORDER BY id DESC LIMIT 20
    stmt = select(Transaction).order_by(Transaction.id.desc()).limit(20)
    transactions = list(session.exec(stmt).all())
    transaction_ids = [item.transaction_id for item in transactions]
    if not transaction_ids:
        return []

    score_results = session.exec(
        select(FraudTypeScoreResult).where(
            FraudTypeScoreResult.transaction_id.in_(transaction_ids)
        )
    ).all()
    score_by_transaction_id = {
        item.transaction_id: item for item in score_results
    }
    return [
        _transaction_response(tx, score_by_transaction_id.get(tx.transaction_id))
        for tx in transactions
    ]


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: str,
    session: SessionDep,
) -> TransactionResponse:
    transaction = session.exec(
        select(Transaction).where(Transaction.transaction_id == transaction_id)
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
    return _transaction_response(transaction, score_result)
