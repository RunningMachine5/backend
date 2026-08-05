from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")


class Transaction(SQLModel, table=True):
    """거래 테이블"""

    __tablename__ = "transactions"

    # @Id @GeneratedValue(strategy = IDENTITY)
    id: int | None = Field(default=None, primary_key=True)

    transaction_id: str = Field(max_length=64, index=True, unique=True)
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    raw_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON_COLUMN, nullable=False),
    )

    # 초기 스켈레톤과 기존 DB 행의 호환성을 위해 남겨 둔다.
    payment_method: str | None = Field(default=None, max_length=32)

    prediction_status: str = Field(default="RECEIVED", max_length=16, index=True)
    ml_is_fraud: bool | None = Field(default=None)
    fraud_probability: float | None = Field(default=None)
    shap: dict[str, float] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    model_name: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=64)

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.now, nullable=False)
