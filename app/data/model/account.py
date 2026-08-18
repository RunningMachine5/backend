from datetime import datetime, UTC

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel


class Account(SQLModel, table=True):
    """내부·외부 계좌 식별 정보와 현재 한도·정지 상태.

    수취 계좌처럼 외부에서 처음 관측되는 계좌는 한도·잔액을 알 수 없으므로
    해당 컬럼을 NULL로 남긴다. 출금 계좌도 원천 데이터가 제공하지 않은 값은
    NULL을 허용한다.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "account_type IS NULL OR account_type IN ('a', 'b', 'c', 'd', 'e')",
            name="ck_accounts_account_type",
        ),
        CheckConstraint(
            "amount_daily_limit IS NULL OR amount_daily_limit >= 0",
            name="ck_accounts_amount_daily_limit_nonnegative",
        ),
    )

    id: int = Field(default=None, primary_key=True)
    customer_id: int | None = Field(
        default=None,
        foreign_key="customers.id",
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
    current_balance: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    amount_daily_limit: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    indicator_openbanking: bool | None = Field(default=None)
    suspend_status: bool = Field(default=False, nullable=False)

    created_at: datetime = Field(
        default_factory=datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Account"]
