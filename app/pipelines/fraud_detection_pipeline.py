"""거래 저장부터 ML 예측과 유형별 룰 점수 저장까지 조정한다."""

from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionCreateDTO
from app.repositories.transaction import (
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

        transaction = self.transaction_repository.add_received(payload)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateTransactionError(payload.transaction_id) from exc
        self.session.refresh(transaction)
        raw_features = transaction.raw_features

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
                shap=prediction.shap,
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
        )


__all__ = [
    "DuplicateTransactionError",
    "FraudDetectionPipeline",
    "FraudDetectionResult",
]
