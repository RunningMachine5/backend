from datetime import date, datetime, UTC

from sqlalchemy import CheckConstraint, Column, Date, DateTime, SmallInteger, func
from sqlmodel import Field, SQLModel


class Customer(SQLModel, table=True):
    """거래와 계좌가 참조하는 고객 원장."""

    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "credit_rating BETWEEN 1 AND 9",
            name="ck_customers_credit_rating",
        ),
        CheckConstraint(
            "loan_type IN ('a', 'b', 'c', 'd', 'e')",
            name="ck_customers_loan_type",
        ),
        CheckConstraint(
            "gender IN ('male', 'female')",
            name="ck_customers_gender",
        ),
    )

    id: int = Field(default=None, primary_key=True)
    # 생성 원본에서 고객 이름으로 사용되는 값이라 동명이인을 허용한다.
    name: str = Field(max_length=32)
    birth_date: date = Field(sa_column=Column(Date, nullable=False))
    gender: str = Field(max_length=16)
    identification_number: str = Field(max_length=32, unique=True)
    phone_number: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    registration_datetime: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    credit_rating: int = Field(sa_column=Column(SmallInteger, nullable=False))
    loan_type: str = Field(max_length=8)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), server_default=func.now(),  nullable=False, onupdate=func.now()),
    )


__all__ = ["Customer"]
