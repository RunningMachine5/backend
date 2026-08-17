"""담당자 탐지 구조에 거래 저장·룰 처리를 이어 붙인 운영 파이프라인."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionRequestDTO
from app.repositories.transaction import (
    PredictionResultRepository,
    TransactionRepository,
)
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClient, MLServingError
from app.services.rules.scoring import score_transaction_fraud_types


@dataclass(frozen=True)
class FraudDetectionResult:
    """API와 Agent가 사용하는 최종 탐지 결과."""

    transaction: Transaction
    prediction_status: str
    prediction_result: MLPredictionResult | None
    score_result: FraudTypeScoreResult | None
    ml_features: dict[str, Any]


class FraudDetectionPipeline:
    """Feature 생성, ML 요청, 저장, 룰 평가 순서만 조정한다."""

    def __init__(
        self,
        *,
        session: Session,
        derived_features_service: DerivedFeatureService,
        ml_client: MLServingClient,
    ) -> None:
        self.session = session
        self.derived_features_service = derived_features_service
        self.ml_client = ml_client
        self.transaction_repository = TransactionRepository(session)
        self.prediction_repository = PredictionResultRepository(session)

    def run(self, payload: TransactionRequestDTO) -> FraudDetectionResult:
        """담당자 설계 순서 뒤에 기존 저장·룰 기능을 실행한다."""

        # 담당자 설계와 동일하게, 아직 저장되지 않은 거래를 기준으로 DB 이력과
        # 고객·계좌 정보를 조회해 ML 입력 Feature를 먼저 만든다.
        assembled = self.derived_features_service.create_derived_features(payload)
        raw_features = assembled.model_dump(mode="json")

        # 현재 ML 계약은 정수 transaction_id를 요구한다. commit 전 flush로 ID만
        # 발급받고, ML·룰 결과까지 준비된 뒤 한 번에 commit한다.
        transaction = self.transaction_repository.add_received(payload, assembled)
        assert transaction.id is not None

        prediction_result: MLPredictionResult | None = None
        score_result: FraudTypeScoreResult | None = None
        prediction_started_at = perf_counter()
        try:
            prediction = self.ml_client.predict(
                transaction_id=transaction.id,
                features=raw_features,
            )
        except MLServingError:
            prediction_status = "FAILED"
            transaction.transaction_failure_status = True
            transaction.error_code = "ML_FAIL"
        else:
            prediction_status = "COMPLETED"
            transaction.transaction_failure_status = prediction.is_fraud
            transaction.error_code = "FRAUD" if prediction.is_fraud else None
            prediction_result = MLPredictionResult(
                transaction_id=transaction.id,
                predict_result=prediction.is_fraud,
                predict_proba=prediction.predict_proba,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                latency_ms=max(
                    0,
                    round((perf_counter() - prediction_started_at) * 1000),
                ),
            )
            self.prediction_repository.add(prediction_result)

            # 룰은 ML 판정을 바꾸지 않고, 사기 거래의 유형별 점수만 계산한다.
            if prediction.is_fraud:
                score_result = score_transaction_fraud_types(
                    session=self.session,
                    transaction_id=transaction.id,
                    features=assembled,
                )
                if score_result is not None:
                    self.session.add(score_result)
            else:
                self.transaction_repository.apply_approved_balance(
                    transaction,
                    assembled,
                )

        self.session.add(transaction)
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


__all__ = ["FraudDetectionPipeline", "FraudDetectionResult"]
