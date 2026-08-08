from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class TransactionLabel(SQLModel, table=True):
    """사후 확정된 거래 정답 라벨."""

    __tablename__ = "transaction_labels"

    transaction_id: str = Field(
        primary_key=True,
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
        max_length=64,
    )
    confirmed_is_fraud: bool
    labeled_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["TransactionLabel"]
