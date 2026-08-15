"""거래 저장 → ML 예측 → 사기유형 룰 점수 저장 흐름을 조정한다."""

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
from app.services.ml_serving.client import MLServingClient, MLServingError
from app.services.rules.scoring import score_transaction_fraud_types

@dataclass(frozen=True)
class FraudDetectionResult:
    """Pipeline이 API 응답 변환에 넘기는 저장 결과."""

    transaction: Transaction
    prediction_status: str
    prediction_result: MLPredictionResult | None
    score_result: FraudTypeScoreResult | None
    # DB의 고객·계좌·거래·파생 테이블에서 조립한 ML 입력값이다.
    ml_features: dict[str, Any] | None


class FraudDetectionPipeline:
    """원본 저장, ML 추론, 룰 점수 계산과 결과 저장 순서를 조정한다."""

    def __init__(self, *, session: Session, ml_client: MLServingClient) -> None:
        self.session = session
        self.ml_client = ml_client
        self.transaction_repository = TransactionRepository(session)
        self.prediction_repository = PredictionResultRepository(session)

    def run(self, payload: TransactionRequestDTO) -> FraudDetectionResult:
        """거래 원본을 보존한 뒤 ML 예측과 선택적 룰 점수를 저장한다.

        처리 상태:
        - FAILED: raw59는 완성됐지만 ML Serving 호출에 실패함
        - COMPLETED: ML 예측을 저장함. 사기 판정일 때만 룰 점수도 저장함
        """

        # 1. ML 장애와 관계없이 수신한 거래 원본을 먼저 확정한다.
        transaction = self.transaction_repository.add_received(payload)
        self.session.commit()
        self.session.refresh(transaction)
        assert transaction.id is not None

        # 2. 저장된 고객·계좌·거래·파생 데이터를 ML 입력 raw59로 조립한다.
        # 현재는 실시간 파생 계산기가 없어 거래 저장 시 생성한 임시 기본값을
        # 사용한다. 고객 원장이나 수취 계좌가 없으면 각각 임시값을 사용한다.
        assembled = self.transaction_repository.load_ml_features(transaction)
        raw_features = assembled.model_dump(mode="json", by_alias=True)

        score_result: FraudTypeScoreResult | None = None
        prediction_result: MLPredictionResult | None = None
        prediction_started_at = perf_counter()
        try:
            # 3. ML 서버는 raw59를 model80으로 전처리한 뒤 예측 결과를 반환한다.
            prediction = self.ml_client.predict(
                transaction_id=transaction.id,
                features=raw_features,
            )
        except MLServingError:
            # 거래 원본은 이미 저장됐으므로 예측 실패 상태만 응답한다.
            prediction_status = "FAILED"
        else:
            latency_ms = max(
                0,
                round((perf_counter() - prediction_started_at) * 1000),
            )
            prediction_status = "COMPLETED"
            prediction_result = MLPredictionResult(
                transaction_id=transaction.id,
                predict_result=prediction.is_fraud,
                predict_proba=prediction.predict_proba,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                latency_ms=latency_ms,
            )
            self.prediction_repository.add(prediction_result)

            # 4. 룰은 ML이 사기로 판정한 거래의 유형을 설명하는 후속 단계다.
            # 정상 거래에는 룰 점수를 만들지 않으며, 룰 결과가 ML 판정을
            # 사기 또는 정상으로 다시 바꾸지도 않는다.
            if prediction.is_fraud:
                score_result = score_transaction_fraud_types(
                    session=self.session,
                    transaction_id=transaction.id,
                    features=assembled,
                )
                if score_result is not None:
                    self.session.add(score_result)

        # ML 결과와 룰 결과는 함께 확정해 서로 다른 상태로 남지 않게 한다.
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
    "FraudDetectionPipeline",
    "FraudDetectionResult",
]
