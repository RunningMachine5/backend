from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    SmallInteger,
)
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, INET_COLUMN, MACADDR_COLUMN


class Transaction(SQLModel, table=True):
    """거래 원본과 거래 시점 계좌 상태 스냅샷.

    잔액 계열 세 컬럼(initial_balance, balance,
    remaining_amount_daily_limit_exceeded)은 accounts의 가변 상태를 거래 시점
    그대로 고정한 값이다. 과거 거래를 재채점할 때는 반드시 이 스냅샷을 읽어야
    하며 accounts의 현재 값을 읽으면 결과가 달라진다.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        Index(
            "ix_transactions_customer_id_transaction_datetime",
            "customer_id",
            "transaction_datetime",
        ),
        Index("ix_transactions_transaction_datetime", "transaction_datetime"),
        CheckConstraint(
            "channel IN ('mobile', 'internet', 'atm', 'others')",
            name="ck_transactions_channel",
        ),
        CheckConstraint(
            "type_general_automatic IN ('general', 'automatic')",
            name="ck_transactions_type_general_automatic",
        ),
        CheckConstraint(
            "access_medium IN ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h')",
            name="ck_transactions_access_medium",
        ),
        CheckConstraint(
            "location_lat IS NULL OR location_lat BETWEEN -90 AND 90",
            name="ck_transactions_location_lat",
        ),
        CheckConstraint(
            "location_lon IS NULL OR location_lon BETWEEN -180 AND 180",
            name="ck_transactions_location_lon",
        ),
        CheckConstraint(
            "num_connection_failure >= 0",
            name="ck_transactions_num_connection_failure",
        ),
        CheckConstraint(
            "initial_balance >= 0",
            name="ck_transactions_initial_balance_nonnegative",
        ),
        CheckConstraint(
            "remaining_amount_daily_limit_exceeded >= 0",
            name="ck_transactions_remaining_daily_limit_nonnegative",
        ),
    )

    # 외부 응답과 모든 자식 FK가 같은 DB 생성 정수 ID를 사용한다.
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    customer_id: str | None = Field(
        default=None,
        foreign_key="customers.id",
        ondelete="RESTRICT",
        max_length=64,
        nullable=True,
    )
    source_account_number: str = Field(
        foreign_key="accounts.account_number",
        ondelete="RESTRICT",
        max_length=255,
        index=True,
    )
    recipient_account_number: str = Field(
        foreign_key="accounts.account_number",
        ondelete="RESTRICT",
        max_length=255,
        index=True,
    )
    transaction_datetime: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    transaction_amount: int = Field(sa_type=BigInteger)

    channel: str = Field(max_length=32)
    type_general_automatic: str = Field(max_length=16)
    access_medium: str | None = Field(max_length=8, nullable=True)
    error_code: str | None = Field(max_length=8, nullable=True)
    num_connection_failure: int = Field(sa_column=Column(SmallInteger, nullable=False))
    another_person_account: bool = Field(default=False, nullable=False)

    # 거래 시점 계좌 상태 스냅샷
    initial_balance: int | None = Field(default=None, sa_type=BigInteger)
    balance: int | None = Field(default=None, sa_type=BigInteger)
    remaining_amount_daily_limit_exceeded: int | None = Field(
        default=None,
        sa_type=BigInteger,
    )

    # 단말·접속 환경
    operating_system: str | None = Field(default=None, max_length=32)
    ip_address: str | None = Field(
        default=None,
        sa_column=Column(INET_COLUMN, nullable=True),
    )
    mac_address: str | None = Field(
        default=None,
        sa_column=Column(MACADDR_COLUMN, nullable=True),
    )
    location_lat: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    location_lon: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    rooting_jailbreak_indicator: bool
    mobile_roaming_indicator: bool
    vpn_indicator: bool
    flag_terminal_malicious_behavior_1: bool
    flag_terminal_malicious_behavior_2: bool
    flag_terminal_malicious_behavior_3: bool
    flag_terminal_malicious_behavior_5: bool
    flag_terminal_malicious_behavior_6: bool

    transaction_failure_status: bool = Field(default=False, nullable=False)

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Transaction"]
