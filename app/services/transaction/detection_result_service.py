"""doo 거래 처리 뒤에 MLOps·룰 결과를 저장한다."""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.fraud_detection import FraudDetectionResponseDTO
from app.dto.ml_features import MLTransactionFeatures
from app.dto.transaction import TransactionResponseDTO
from app.services.ml_serving.client import MLPredictionResponse
from app.services.rules.scoring import score_transaction_fraud_types


@dataclass(frozen=True)
class FraudDetectionResult:
    """API와 Agent가 함께 사용하는 최종 탐지 결과."""

    response: FraudDetectionResponseDTO
    transaction: Transaction
    prediction_result: MLPredictionResult
    score_result: FraudTypeScoreResult | None


class DetectionResultService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(
        self,
        *,
        transaction_response: TransactionResponseDTO,
        prediction: MLPredictionResponse,
        features: MLTransactionFeatures,
        latency_ms: int,
    ) -> FraudDetectionResult:
        """ML 결과와 사기 거래의 룰 점수를 저장한다."""

        transaction = self.session.exec(
            select(Transaction).where(
                Transaction.id == transaction_response.transaction_id
            )
        ).one()
        is_fraud = transaction_response.prediction_status == "DECLINED"

        prediction_result = MLPredictionResult(
            transaction_id=transaction_response.transaction_id,
            predict_result=is_fraud,
            predict_proba=prediction.predict_proba,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            latency_ms=latency_ms,
        )
        self.session.add(prediction_result)

        score_result = None
        if is_fraud:
            score_result = score_transaction_fraud_types(
                session=self.session,
                transaction_id=transaction_response.transaction_id,
                features=features,
            )
            if score_result is not None:
                self.session.add(score_result)

        self.session.commit()
        self.session.refresh(transaction)
        self.session.refresh(prediction_result)
        if score_result is not None:
            self.session.refresh(score_result)

        response = FraudDetectionResponseDTO(
            **transaction_response.model_dump(),
            predict_result=is_fraud,
            rule_set_id=score_result.rule_set_id if score_result else None,
            rule_scores=score_result.type_scores if score_result else None,
            created_at=transaction.created_at,
        )
        return FraudDetectionResult(
            response=response,
            transaction=transaction,
            prediction_result=prediction_result,
            score_result=score_result,
        )


__all__ = ["DetectionResultService", "FraudDetectionResult"]
