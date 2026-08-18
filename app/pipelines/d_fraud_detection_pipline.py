"""doo 거래 흐름에 MLOps 결과 저장과 룰 분류를 연결한다."""

from dataclasses import dataclass
from time import perf_counter

from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionRequestDTO
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClient
from app.services.rules.scoring import score_transaction_fraud_types
from app.services.transaction.transaction_service import TransactionService


@dataclass(frozen=True)
class FraudDetectionResult:
    """API와 Agent가 함께 사용하는 거래 탐지 결과."""

    transaction: Transaction
    prediction_result: MLPredictionResult
    score_result: FraudTypeScoreResult | None


class DFraudDetectionPipeline:
    def __init__(
        self,
        session: Session,
        derived_features_service: DerivedFeatureService,
        ml_serving_client: MLServingClient,
        transaction_service: TransactionService,
    ) -> None:
        self.session = session
        self.derived_features_service = derived_features_service
        self.ml_serving_client = ml_serving_client
        self.transaction_service = transaction_service

    def run(self, transaction: TransactionRequestDTO) -> FraudDetectionResult:
        # 1. DB 정보와 요청값을 합쳐 ML 입력, 거래, 파생 피처를 만든다.
        ml_features, transaction_data, derived_data = (
            self.derived_features_service.create_derived_features(transaction)
        )

        # 2. ML 서버에 이상거래 확률을 요청한다.
        started_at = perf_counter()
        prediction = self.ml_serving_client.predict(
            features=ml_features.model_dump(mode="json")
        )
        latency_ms = round((perf_counter() - started_at) * 1000)

        # 3. ML 서버의 최종 판정으로 거래 상태를 정하고 거래를 저장한다.
        stored_transaction, is_fraud = self.transaction_service.save_transaction(
            transaction_data,
            prediction,
        )
        self.derived_features_service.save_derived_features(
            derived_data,
            stored_transaction.id,
        )

        # 4. 어떤 모델이 판단했는지 MLOps 조회용 결과를 저장한다.
        prediction_result = MLPredictionResult(
            transaction_id=stored_transaction.id,
            predict_result=bool(prediction.predict_result),
            predict_proba=prediction.predict_proba,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            latency_ms=latency_ms,
        )
        self.session.add(prediction_result)

        # 5. 이상거래만 유형별 룰 점수를 계산한다.
        score_result = None
        if is_fraud:
            score_result = score_transaction_fraud_types(
                session=self.session,
                transaction_id=stored_transaction.id,
                features=ml_features,
            )
            if score_result is not None:
                self.session.add(score_result)

        self.session.commit()
        self.session.refresh(stored_transaction)
        self.session.refresh(prediction_result)
        if score_result is not None:
            self.session.refresh(score_result)

        return FraudDetectionResult(
            transaction=stored_transaction,
            prediction_result=prediction_result,
            score_result=score_result,
        )


__all__ = ["DFraudDetectionPipeline", "FraudDetectionResult"]

