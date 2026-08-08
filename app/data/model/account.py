from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Account(SQLModel, table=True):
    """내부·외부 계좌 식별 정보."""

    __tablename__ = "accounts"

    account_id: str = Field(primary_key=True, max_length=64)
    customer_id: str | None = Field(
        default=None,
        foreign_key="customers.customer_id",
        ondelete="SET NULL",
        max_length=64,
        index=True,
    )
    account_number: str = Field(max_length=255, unique=True)
    account_type: str | None = Field(default=None, max_length=32)
    creation_datetime: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Account"]
