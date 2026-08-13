from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel


class Account(SQLModel, table=True):
    """내부·외부 계좌 식별 정보와 현재 한도·정지 상태.

    수취 계좌처럼 외부에서 처음 관측되는 계좌는 한도·잔액을 알 수 없으므로
    해당 컬럼을 NULL로 남긴다. 룰 평가가 읽는 값은 항상 출금 계좌 쪽이며,
    출금 계좌는 거래 요청의 raw59 Feature로 전부 채워진다.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "account_type IS NULL OR account_type IN ('a', 'b', 'c', 'd')",
            name="ck_accounts_account_type",
        ),
        CheckConstraint(
            "amount_daily_limit IS NULL OR amount_daily_limit >= 0",
            name="ck_accounts_amount_daily_limit_nonnegative",
        ),
        CheckConstraint(
            "remaining_daily_limit IS NULL OR remaining_daily_limit >= 0",
            name="ck_accounts_remaining_daily_limit_nonnegative",
        ),
    )

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
    amount_daily_limit: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    indicator_openbanking: bool | None = Field(default=None)
    indicator_release_limit_excess: bool | None = Field(default=None)
    # 아래 세 컬럼은 거래마다 갱신되는 가변 상태다. 과거 거래를 재평가할 때는
    # 이 값이 아니라 transactions·derived_features의 스냅샷을 사용해야 한다.
    current_balance: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    remaining_daily_limit: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    suspend_status: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Account"]
