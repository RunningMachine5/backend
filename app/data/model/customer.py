from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime
from sqlmodel import Field, SQLModel


class Customer(SQLModel, table=True):
    """거래와 계좌가 참조하는 고객 원장."""

    __tablename__ = "customers"

    customer_id: str = Field(primary_key=True, max_length=64)
    birth_date: date = Field(sa_column=Column(Date, nullable=False))
    gender: str = Field(max_length=16)
    # 생성 원본에서 고객 이름으로 사용되는 값이라 동명이인을 허용한다.
    personal_identifier: str = Field(max_length=255)
    identification_number: str = Field(max_length=255, unique=True)
    registration_datetime: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    credit_rating: str = Field(max_length=32)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Customer"]
