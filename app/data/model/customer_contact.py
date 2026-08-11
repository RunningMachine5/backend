"""고객(주민등록번호 기준 원장)에 대응하는 이메일·전화번호 연락처."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class CustomerContact(SQLModel, table=True):
    """customers와 1:1로 연결되는 연락처 정보."""

    __tablename__ = "customer_contacts"

    customer_id: str = Field(
        primary_key=True,
        foreign_key="customers.customer_id",
        ondelete="CASCADE",
        max_length=64,
    )
    email: str = Field(max_length=255)
    phone_number: str = Field(max_length=32)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["CustomerContact"]
