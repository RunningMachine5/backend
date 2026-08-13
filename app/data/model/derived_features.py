"""거래 한 건에 대해 사전 계산된 윈도우·조인 파생 피처 (transactions와 1:1)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Float
from sqlmodel import Field, SQLModel

from app.data.model.types import INTERVAL_COLUMN


class DerivedFeatures(SQLModel, table=True):
    """거래 시점에 확정된 파생 피처 스냅샷.

    윈도우 집계(1개월 거래통계, 90일 인증변경, 7일 ATM 한도, 30일 정지해제)와
    계좌·고객 조인 결과를 거래 시점 값 그대로 고정한다. 원본 테이블을 다시
    집계하면 값이 달라지므로 재채점은 이 테이블을 읽어야 한다.

    컬럼명은 ERD 원문의 오타(release_suspention, inquery_atm_limit)를 바로잡아
    정타로 저장한다. 외부 CSV·ML 계약의 오타 이름은 입력 계층에서만 흡수한다.
    """

    __tablename__ = "derived_features"
    __table_args__ = (
        CheckConstraint(
            "distance >= 0", name="ck_derived_features_distance_nonnegative"
        ),
        CheckConstraint(
            "one_month_max_amount >= 0",
            name="ck_derived_features_month_max_nonnegative",
        ),
        CheckConstraint(
            "one_month_std_dev >= 0",
            name="ck_derived_features_month_std_nonnegative",
        ),
        CheckConstraint(
            "dawn_one_month_max_amount >= 0",
            name="ck_derived_features_dawn_max_nonnegative",
        ),
        CheckConstraint(
            "dawn_one_month_std_dev >= 0",
            name="ck_derived_features_dawn_std_nonnegative",
        ),
        CheckConstraint(
            "number_of_transaction_with_the_account >= 0",
            name="ck_derived_features_recent_count_nonnegative",
        ),
        CheckConstraint(
            "transaction_history_with_the_account >= 0",
            name="ck_derived_features_history_count_nonnegative",
        ),
    )

    id: str = Field(
        primary_key=True,
        foreign_key="transactions.id",
        ondelete="CASCADE",
        max_length=64,
    )

    # TXN 직전 1건
    distance: float = Field(sa_column=Column(Float, nullable=False))
    time_difference: timedelta = Field(
        sa_column=Column(INTERVAL_COLUMN, nullable=False)
    )

    # TXN 1개월 (전체 / 새벽)
    one_month_max_amount: int = Field(sa_type=BigInteger)
    one_month_std_dev: float = Field(sa_column=Column(Float, nullable=False))
    dawn_one_month_max_amount: int = Field(sa_type=BigInteger)
    dawn_one_month_std_dev: float = Field(sa_column=Column(Float, nullable=False))

    # 기존 거래 전체
    unused_terminal_status: bool
    unused_account_status: bool
    transaction_history_with_the_account: int

    # TXN 7일
    flag_deposit_more_than_tenMillion: bool

    # TXN 3시간 / 누적
    number_of_transaction_with_the_account: int

    # TXN 채널 이력
    last_atm_transaction_datetime: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_bank_branch_transaction_datetime: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # EVENT 90일
    flag_change_of_authentication_1: bool
    flag_change_of_authentication_2: bool
    flag_change_of_authentication_3: bool
    flag_change_of_authentication_4: bool

    # EVENT 7일 (ERD 원문 inquery_atm_limit의 오타를 바로잡음)
    inquiry_atm_limit: bool
    increase_atm_limit: bool

    # EVENT 30일 (ERD 원문 release_suspention의 오타를 바로잡음)
    release_suspension: bool
    transaction_resumed_date: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # 조인 결과 스냅샷
    recipient_account_suspend_status: bool
    first_time_ios_by_vulnerable_user: bool

    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["DerivedFeatures"]
