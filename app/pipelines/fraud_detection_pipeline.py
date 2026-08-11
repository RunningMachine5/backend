"""거래 저장부터 ML 예측과 유형별 룰 점수 저장까지 조정한다."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionCreateDTO
from app.repositories.transaction import (
    CustomerIdentificationConflictError,
    PredictionResultRepository,
    TransactionRepository,
)
from app.services.ml_serving.client import MLServingClient, MLServingError
from app.services.rules.scoring import score_transaction_fraud_types


class DuplicateTransactionError(RuntimeError):
    """같은 transaction_id의 거래가 이미 저장된 경우."""


@dataclass(frozen=True)
class FraudDetectionResult:
    """Pipeline이 API 응답 변환에 넘기는 저장 결과."""

    transaction: Transaction
    prediction_status: str
    prediction_result: MLPredictionResult | None
    score_result: FraudTypeScoreResult | None
    # 평탄화된 컬럼을 다시 조회하지 않도록 요청에서 받은 54개 Feature를 넘긴다.
    ml_features: dict[str, Any]


class FraudDetectionPipeline:
    """원본 저장, ML 추론, 룰 점수 계산과 결과 저장 순서를 조정한다."""

    def __init__(self, *, session: Session, ml_client: MLServingClient) -> None:
        self.session = session
        self.ml_client = ml_client
        self.transaction_repository = TransactionRepository(session)
        self.prediction_repository = PredictionResultRepository(session)

    def run(self, payload: TransactionCreateDTO) -> FraudDetectionResult:
        """거래 원본을 보존한 뒤 ML 예측과 선택적 룰 점수를 저장한다."""

        if self.transaction_repository.get(payload.transaction_id) is not None:
            raise DuplicateTransactionError(payload.transaction_id)

        try:
            # add_received 내부의 조회가 pending INSERT를 autoflush할 수 있으므로
            # 저장 구성부터 commit까지 같은 IntegrityError 경계로 묶는다.
            transaction = self.transaction_repository.add_received(payload)
            self.session.commit()
        except CustomerIdentificationConflictError:
            self.session.rollback()
            raise
        except IntegrityError as exc:
            self.session.rollback()
            constraint_name = getattr(
                getattr(exc.orig, "diag", None),
                "constraint_name",
                None,
            )
            error_message = str(exc.orig)
            if (
                constraint_name == "uq_customers_identification_number"
                or "customers.identification_number" in error_message
            ):
                raise CustomerIdentificationConflictError(
                    payload.customer_identification_number
                ) from exc
            if (
                constraint_name in {"transactions_pkey", "pk_transactions"}
                or "transactions.transaction_id" in error_message
            ):
                raise DuplicateTransactionError(payload.transaction_id) from exc
            raise
        self.session.refresh(transaction)
        # 저장 직후에는 요청 본문의 Feature가 DB 재조립 결과와 동일하므로
        # 조회를 한 번 아끼기 위해 그대로 사용한다.
        raw_features = payload.raw_features.model_dump(mode="json", by_alias=True)

        score_result: FraudTypeScoreResult | None = None
        prediction_result: MLPredictionResult | None = None
        prediction_started_at = perf_counter()
        try:
            prediction = self.ml_client.predict(
                transaction_id=transaction.transaction_id,
                features=raw_features,
            )
        except MLServingError:
            prediction_status = "FAILED"
        else:
            latency_ms = max(
                0,
                round((perf_counter() - prediction_started_at) * 1000),
            )
            prediction_status = "COMPLETED"
            prediction_result = MLPredictionResult(
                transaction_id=transaction.transaction_id,
                prediction_is_fraud=prediction.is_fraud,
                fraud_probability=prediction.fraud_probability,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                latency_ms=latency_ms,
            )
            self.prediction_repository.add(prediction_result)

            if prediction.is_fraud:
                score_result = score_transaction_fraud_types(
                    session=self.session,
                    transaction_id=transaction.transaction_id,
                    raw_data=raw_features,
                )
                if score_result is not None:
                    self.session.add(score_result)

        self.session.commit()
        if prediction_result is not None:
            self.session.refresh(prediction_result)
        return FraudDetectionResult(
            transaction=transaction,
            prediction_status=prediction_status,
            prediction_result=prediction_result,
            score_result=score_result,
            ml_features=raw_features,
        )


__all__ = [
    "CustomerIdentificationConflictError",
    "DuplicateTransactionError",
    "FraudDetectionPipeline",
    "FraudDetectionResult",
]
