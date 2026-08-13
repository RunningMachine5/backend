from datetime import date, datetime

from sqlalchemy import CheckConstraint, Column, Date, DateTime, SmallInteger
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

    customer_id: str = Field(primary_key=True, max_length=64)
    birth_date: date = Field(sa_column=Column(Date, nullable=False))
    gender: str = Field(max_length=16)
    # 생성 원본에서 고객 이름으로 사용되는 값이라 동명이인을 허용한다.
    personal_identifier: str = Field(max_length=255)
    identification_number: str = Field(max_length=255, unique=True)
    registration_datetime: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    credit_rating: int = Field(sa_column=Column(SmallInteger, nullable=False))
    loan_type: str = Field(max_length=8)
    email: str | None = Field(default=None, max_length=255)
    phone_number: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Customer"]
