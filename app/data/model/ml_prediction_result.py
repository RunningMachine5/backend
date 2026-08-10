"""거래별 ML Serving 추론 결과 영속 모델."""

from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class MLPredictionResult(SQLModel, table=True):
    """한 번의 온라인 ML 추론 결과를 저장한다."""

    __tablename__ = "ml_prediction_results"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    transaction_id: str = Field(
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
        max_length=64,
        index=True,
    )
    prediction_is_fraud: bool
    fraud_probability: float = Field(ge=0.0, le=1.0)
    model_name: str = Field(max_length=128)
    model_version: str = Field(max_length=64)
    latency_ms: int = Field(ge=0)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


__all__ = ["MLPredictionResult"]
