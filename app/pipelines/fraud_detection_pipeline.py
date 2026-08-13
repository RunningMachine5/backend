"""거래 저장부터 ML 예측과 유형별 룰 점수 저장까지 조정한다."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.data.model.customer import Customer
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionRequestDTO
from app.repositories.transaction import (
    CustomerIdentificationConflictError,
    PredictionResultRepository,
    TransactionRepository,
)
from app.services.ml_serving.client import MLServingClient, MLServingError
from app.services.rules.scoring import score_transaction_fraud_types


class DuplicateTransactionError(RuntimeError):
    """같은 transaction_id의 거래가 이미 저장된 경우."""


_TRANSACTION_UNIQUE_CONSTRAINTS = {"transactions_pkey", "pk_transactions"}
_MASTER_RACE_CONSTRAINTS = {
    "customers_pkey",
    "pk_customers",
    "accounts_pkey",
    "pk_accounts",
    "uq_accounts_account_number",
}


def _integrity_error_details(exc: IntegrityError) -> tuple[str | None, str]:
    """PostgreSQL constraint 이름과 SQLite 회귀 테스트 메시지를 정규화한다."""

    constraint_name = getattr(
        getattr(exc.orig, "diag", None),
        "constraint_name",
        None,
    )
    return constraint_name, str(exc.orig)


def _is_transaction_unique_violation(
    constraint_name: str | None,
    error_message: str,
) -> bool:
    return (
        constraint_name in _TRANSACTION_UNIQUE_CONSTRAINTS
        or "transactions.transaction_id" in error_message
    )


def _is_customer_identification_violation(
    constraint_name: str | None,
    error_message: str,
) -> bool:
    return (
        constraint_name == "uq_customers_identification_number"
        or "customers.identification_number" in error_message
    )


def _is_retryable_master_race(
    constraint_name: str | None,
    error_message: str,
) -> bool:
    return (
        constraint_name in _MASTER_RACE_CONSTRAINTS
        or "customers.customer_id" in error_message
        or "accounts.account_id" in error_message
        or "accounts.account_number" in error_message
    )


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

    def run(self, payload: TransactionRequestDTO) -> FraudDetectionResult:
        """거래 원본을 보존한 뒤 ML 예측과 선택적 룰 점수를 저장한다."""

        if self.transaction_repository.get(payload.transaction_id) is not None:
            raise DuplicateTransactionError(payload.transaction_id)

        transaction: Transaction | None = None
        for attempt in range(2):
            try:
                # add_received 내부의 조회가 pending INSERT를 autoflush할 수 있으므로
                # 저장 구성부터 commit까지 같은 IntegrityError 경계로 묶는다.
                transaction = self.transaction_repository.add_received(payload)
                self.session.commit()
                break
            except CustomerIdentificationConflictError:
                self.session.rollback()
                raise
            except IntegrityError as exc:
                self.session.rollback()
                constraint_name, error_message = _integrity_error_details(exc)

                # 최초 조회 이후 같은 transaction_id가 먼저 commit된 경우에도
                # 기존 409 계약을 지키며 master race로 오인해 재시도하지 않는다.
                if self.transaction_repository.get(payload.transaction_id) is not None:
                    raise DuplicateTransactionError(payload.transaction_id) from exc
                if _is_transaction_unique_violation(
                    constraint_name,
                    error_message,
                ):
                    raise DuplicateTransactionError(payload.transaction_id) from exc

                if _is_customer_identification_violation(
                    constraint_name,
                    error_message,
                ):
                    # 동일 신규 고객끼리 경합하면 PK보다 identification unique가
                    # 먼저 보고될 수도 있다. 승자 행이 같은 식별번호라면 한 번
                    # 정상 upsert로 재실행하고, 다른 고객의 번호면 기존 409다.
                    customer = self.session.get(Customer, payload.customer_id)
                    if (
                        customer is None
                        or customer.identification_number
                        != payload.customer_identification_number
                    ):
                        raise CustomerIdentificationConflictError(
                            payload.customer_identification_number
                        ) from exc
                elif not _is_retryable_master_race(
                    constraint_name,
                    error_message,
                ):
                    raise

                if attempt == 1:
                    raise

        assert transaction is not None
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
