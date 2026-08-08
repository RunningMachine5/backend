from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, select

from app.core.db import SessionDep
from app.data.model.transaction import Transaction
from app.dto.ml_prediction import MLTransactionFeatures
from app.services.ml_serving.client import MLServingClientDep, MLServingError
from app.services.rules.classification import classify_transaction_fraud_type

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


class TransactionCreate(SQLModel):
    """전처리 전 ML 원본 Feature 54개를 포함하는 거래 수신 DTO."""

    transaction_id: str = Field(min_length=1, max_length=64)
    occurred_at: datetime | None = None
    raw_data: MLTransactionFeatures


@router.post("", response_model=Transaction, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    session: SessionDep,
    ml_client: MLServingClientDep,
) -> Transaction:
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

        fraud_type_result = classify_transaction_fraud_type(
            session=session,
            transaction_id=tx.transaction_id,
            raw_data=raw_data,
            is_fraud=prediction.is_fraud,
        )
        session.add(fraud_type_result)

    tx.updated_at = datetime.now()
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


@router.get("", response_model=list[Transaction])
def list_transactions(session: SessionDep) -> list[Transaction]:
    # SELECT * FROM transactions ORDER BY id DESC LIMIT 20
    stmt = select(Transaction).order_by(Transaction.id.desc()).limit(20)
    return list(session.exec(stmt).all())
