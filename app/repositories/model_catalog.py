"""저장된 모델 버전별 온라인 처리 결과를 조회한다."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func
from sqlmodel import Session, select

from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel


@dataclass(frozen=True)
class ModelUsageSummary:
    processed_transaction_count: int
    fraud_prediction_count: int
    labeled_transaction_count: int
    matching_label_count: int
    false_positive_count: int
    false_negative_count: int
    average_latency_ms: float | None
    first_inference_at: datetime | None
    latest_inference_at: datetime | None


class ModelCatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _latest_predictions():
        """같은 모델이 같은 거래를 여러 번 처리했다면 최신 결과만 남긴다."""

        return select(
            MLPredictionResult.id.label("prediction_result_id"),
            func.row_number()
            .over(
                partition_by=(
                    MLPredictionResult.model_name,
                    MLPredictionResult.model_version,
                    MLPredictionResult.transaction_id,
                ),
                order_by=(
                    MLPredictionResult.created_at.desc(),
                    MLPredictionResult.id.desc(),
                ),
            )
            .label("prediction_rank"),
        ).subquery()

    def usage_by_model_version(self) -> dict[tuple[str, str], ModelUsageSummary]:
        """온라인 처리 이력과 담당자 확정 라벨을 모델 버전별로 집계한다."""

        latest_predictions = self._latest_predictions()
        rows = self.session.exec(
            select(
                MLPredictionResult.model_name,
                MLPredictionResult.model_version,
                func.count(),
                func.sum(
                    case((MLPredictionResult.predict_result.is_(True), 1), else_=0)
                ),
                func.sum(
                    case((TransactionLabel.transaction_id.is_not(None), 1), else_=0)
                ),
                func.sum(
                    case(
                        (
                            and_(
                                TransactionLabel.transaction_id.is_not(None),
                                TransactionLabel.confirmed_is_fraud
                                == MLPredictionResult.predict_result,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                MLPredictionResult.predict_result.is_(True),
                                TransactionLabel.confirmed_is_fraud.is_(False),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                MLPredictionResult.predict_result.is_(False),
                                TransactionLabel.confirmed_is_fraud.is_(True),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.avg(MLPredictionResult.latency_ms),
                func.min(MLPredictionResult.created_at),
                func.max(MLPredictionResult.created_at),
            )
            .join(
                latest_predictions,
                and_(
                    latest_predictions.c.prediction_result_id
                    == MLPredictionResult.id,
                    latest_predictions.c.prediction_rank == 1,
                ),
            )
            .outerjoin(
                TransactionLabel,
                TransactionLabel.transaction_id
                == MLPredictionResult.transaction_id,
            )
            .group_by(
                MLPredictionResult.model_name,
                MLPredictionResult.model_version,
            )
        ).all()

        return {
            (model_name, model_version): ModelUsageSummary(
                processed_transaction_count=int(processed_count),
                fraud_prediction_count=int(fraud_count or 0),
                labeled_transaction_count=int(labeled_count or 0),
                matching_label_count=int(matching_count or 0),
                false_positive_count=int(false_positive_count or 0),
                false_negative_count=int(false_negative_count or 0),
                average_latency_ms=(
                    round(float(average_latency_ms), 1)
                    if average_latency_ms is not None
                    else None
                ),
                first_inference_at=first_inference_at,
                latest_inference_at=latest_inference_at,
            )
            for (
                model_name,
                model_version,
                processed_count,
                fraud_count,
                labeled_count,
                matching_count,
                false_positive_count,
                false_negative_count,
                average_latency_ms,
                first_inference_at,
                latest_inference_at,
            ) in rows
        }

    def list_transactions(
        self,
        *,
        model_name: str,
        model_version: str,
        label_filter: str,
        offset: int,
        limit: int,
    ) -> tuple[
        list[tuple[Transaction, MLPredictionResult, TransactionLabel | None]],
        int,
    ]:
        """선택 모델이 처리한 거래와 나중에 확정된 담당자 판정을 함께 조회한다."""

        latest_predictions = self._latest_predictions()
        statement = (
            select(Transaction, MLPredictionResult, TransactionLabel)
            .select_from(MLPredictionResult)
            .join(
                latest_predictions,
                and_(
                    latest_predictions.c.prediction_result_id
                    == MLPredictionResult.id,
                    latest_predictions.c.prediction_rank == 1,
                ),
            )
            .join(Transaction, Transaction.id == MLPredictionResult.transaction_id)
            .outerjoin(
                TransactionLabel,
                TransactionLabel.transaction_id == Transaction.id,
            )
            .where(
                MLPredictionResult.model_name == model_name,
                MLPredictionResult.model_version == model_version,
            )
        )
        if label_filter == "LABELED":
            statement = statement.where(TransactionLabel.transaction_id.is_not(None))
        elif label_filter == "MISMATCH":
            statement = statement.where(
                TransactionLabel.transaction_id.is_not(None),
                TransactionLabel.confirmed_is_fraud
                != MLPredictionResult.predict_result,
            )

        total_count = self.session.exec(
            select(func.count()).select_from(statement.subquery())
        ).one()
        rows = self.session.exec(
            statement.order_by(
                Transaction.transaction_datetime.desc(),
                Transaction.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total_count


__all__ = ["ModelCatalogRepository", "ModelUsageSummary"]
