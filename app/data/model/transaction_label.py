from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class TransactionLabel(SQLModel, table=True):
    """사후 확정된 거래 정답 라벨."""

    __tablename__ = "transaction_labels"

    transaction_id: int = Field(
        primary_key=True,
        foreign_key="transactions.id",
        ondelete="CASCADE",
        sa_type=BIGINT_PRIMARY_KEY,
    )
    confirmed_is_fraud: bool
    labeled_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["TransactionLabel"]
