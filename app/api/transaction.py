from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.db import SessionDep
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.transaction import (
    TransactionCreateDTO,
    TransactionLabelResponseDTO,
    TransactionLabelUpdateDTO,
    TransactionResponseDTO,
)
from app.pipelines.fraud_detection_pipeline import (
    CustomerIdentificationConflictError,
    DuplicateTransactionError,
    FraudDetectionPipeline,
)
from app.repositories.transaction import (
    AccountIdentifierConflictError,
    AccountOwnershipConflictError,
    PredictionResultRepository,
    TransactionLabelRepository,
    TransactionRepository,
)
from app.services.ml_serving.client import MLServingClientDep

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


def _dumped_features(
    repository: TransactionRepository,
    transaction: Transaction,
) -> dict[str, Any] | None:
    """정규화 컬럼에서 조립한 raw59 Feature를 응답용 JSON dict로 바꾼다."""

    features = repository.load_ml_features(transaction)
    if features is None:
        return None
    return features.model_dump(mode="json", by_alias=True)


def _transaction_response(
    transaction: Transaction,
    prediction_result: MLPredictionResult | None,
    score_result: FraudTypeScoreResult | None,
    label: TransactionLabel | None,
    *,
    prediction_status: str | None = None,
    ml_features: dict[str, Any] | None = None,
) -> TransactionResponseDTO:
    return TransactionResponseDTO.model_validate(
        {
            # SQLAlchemy가 commit 뒤 객체를 expire하면 SQLModel.model_dump()가
            # 빈 dict를 반환할 수 있다. 응답 계약의 필드를 명시적으로 읽어
            # 세션 상태와 관계없이 같은 응답을 만든다.
            "transaction_id": transaction.id,
            "customer_id": transaction.customer_id,
            # 외부 응답 필드명은 기존 클라이언트 호환을 위해 유지하지만,
            # 값은 새 거래 FK인 계좌번호를 사용한다.
            "source_account_id": transaction.source_account_number,
            "recipient_account_id": transaction.recipient_account_number,
            "transaction_datetime": transaction.transaction_datetime,
            "transaction_amount": transaction.transaction_amount,
            "channel": transaction.channel,
            "location": transaction.location,
            "raw_features": ml_features,
            "created_at": transaction.created_at,
            "prediction_status": prediction_status or (
                "COMPLETED" if prediction_result else "NOT_AVAILABLE"
            ),
            "ml_is_fraud": (
                prediction_result.prediction_is_fraud
                if prediction_result
                else None
            ),
            "fraud_probability": (
                prediction_result.fraud_probability
                if prediction_result
                else None
            ),
            "model_name": (
                prediction_result.model_name if prediction_result else None
            ),
            "model_version": (
                prediction_result.model_version if prediction_result else None
            ),
            "latency_ms": (
                prediction_result.latency_ms if prediction_result else None
            ),
            "rule_scores": score_result.type_scores if score_result else None,
            "rule_set_id": score_result.rule_set_id if score_result else None,
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
    payload: TransactionCreateDTO,
    session: SessionDep,
    ml_client: MLServingClientDep,
) -> TransactionResponseDTO:
    """HTTP 요청을 실제 사기 탐지 Pipeline에 전달한다."""

    pipeline = FraudDetectionPipeline(session=session, ml_client=ml_client)
    try:
        result = pipeline.run(payload)
    except DuplicateTransactionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 존재하는 transaction_id입니다.",
        ) from exc
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
    return _transaction_response(
        result.transaction,
        result.prediction_result,
        result.score_result,
        TransactionLabelRepository(session).get(
            result.transaction.id
        ),
        prediction_status=result.prediction_status,
        ml_features=result.ml_features,
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
    prediction_by_transaction_id: dict[str, MLPredictionResult] = {}
    for item in prediction_results:
        prediction_by_transaction_id.setdefault(item.transaction_id, item)

    score_results = session.exec(
        select(FraudTypeScoreResult).where(
            FraudTypeScoreResult.transaction_id.in_(transaction_ids)
        )
    ).all()
    score_by_transaction_id = {
        item.transaction_id: item for item in score_results
    }
    labels = session.exec(
        select(TransactionLabel).where(
            TransactionLabel.transaction_id.in_(transaction_ids)
        )
    ).all()
    label_by_transaction_id = {
        item.transaction_id: item for item in labels
    }
    repository = TransactionRepository(session)
    return [
        _transaction_response(
            tx,
            prediction_by_transaction_id.get(tx.id),
            score_by_transaction_id.get(tx.id),
            label_by_transaction_id.get(tx.id),
            ml_features=_dumped_features(repository, tx),
        )
        for tx in transactions
    ]


@router.put(
    "/{transaction_id}/label",
    response_model=TransactionLabelResponseDTO,
)
def upsert_transaction_label(
    transaction_id: str,
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
    transaction_id: str,
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
    prediction_result = PredictionResultRepository(
        session
    ).latest_for_transaction(transaction_id)
    label = TransactionLabelRepository(session).get(transaction_id)
    return _transaction_response(
        transaction,
        prediction_result,
        score_result,
        label,
        ml_features=_dumped_features(
            TransactionRepository(session),
            transaction,
        ),
    )
