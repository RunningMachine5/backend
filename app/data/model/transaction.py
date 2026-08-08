from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, JSON, Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")


class Transaction(SQLModel, table=True):
    """ML 추론 전 거래 원본과 Feature 스냅샷."""

    __tablename__ = "transactions"

    transaction_id: str = Field(primary_key=True, max_length=64)
    customer_id: str = Field(
        foreign_key="customers.customer_id",
        ondelete="RESTRICT",
        max_length=64,
        index=True,
    )
    source_account_id: str = Field(
        foreign_key="accounts.account_id",
        ondelete="RESTRICT",
        max_length=64,
        index=True,
    )
    recipient_account_id: str | None = Field(
        default=None,
        foreign_key="accounts.account_id",
        ondelete="SET NULL",
        max_length=64,
        index=True,
    )
    transaction_datetime: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    transaction_amount: int = Field(sa_type=BigInteger)
    channel: str = Field(max_length=32)
    location: str = Field(sa_column=Column(Text, nullable=False))
    raw_features: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON_COLUMN, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
