"""align transactions with ML raw60

ML 담당자의 raw60/raw64 계약에 DB 원장을 맞춘다.

* customers.birthyear를 실제 생년월일 birth_date로 교체
* channel을 소문자 canonical 값으로 통일
* operating_system을 ML 계약처럼 nullable로 변경
* error_code의 폐쇄형 제약을 제거
* 거래금액은 양수, 잔액을 제외한 금액·통계 값은 음수가 아니도록 보강

Revision ID: 8f4d2c6a1b90
Revises: 777dfd5d8992
Create Date: 2026-08-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f4d2c6a1b90"
down_revision: str | Sequence[str] | None = "777dfd5d8992"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("birth_date", sa.Date(), nullable=True))
    # 기존 테스트 데이터는 일 정보가 없으므로 1월 1일로만 백필한다. 신규 raw64
    # 거래부터는 실제 customer_birth_date가 저장된다.
    op.execute("UPDATE customers SET birth_date = make_date(birthyear::int, 1, 1)")
    op.alter_column("customers", "birth_date", nullable=False)
    op.drop_column("customers", "birthyear")

    op.drop_constraint("ck_transactions_channel", "transactions", type_="check")
    op.execute("UPDATE transactions SET channel = lower(channel)")
    op.create_check_constraint(
        "ck_transactions_channel",
        "transactions",
        "channel IN ('mobile', 'internet', 'atm', 'others')",
    )

    op.drop_constraint("ck_transactions_error_code", "transactions", type_="check")
    op.alter_column(
        "transactions",
        "error_code",
        type_=sa.String(length=64),
        existing_type=sa.String(length=8),
    )
    op.alter_column(
        "transactions",
        "operating_system",
        existing_type=sa.String(length=32),
        nullable=True,
    )

    _create_amount_constraints()


def _create_amount_constraints() -> None:
    constraints = (
        (
            "ck_transactions_transaction_amount_positive",
            "transactions",
            "transaction_amount > 0",
        ),
        (
            "ck_transactions_initial_balance_nonnegative",
            "transactions",
            "initial_balance >= 0",
        ),
        (
            "ck_transactions_remaining_daily_limit_nonnegative",
            "transactions",
            "remaining_amount_daily_limit_exceeded >= 0",
        ),
        (
            "ck_accounts_amount_daily_limit_nonnegative",
            "accounts",
            "amount_daily_limit IS NULL OR amount_daily_limit >= 0",
        ),
        (
            "ck_accounts_remaining_daily_limit_nonnegative",
            "accounts",
            "remaining_daily_limit IS NULL OR remaining_daily_limit >= 0",
        ),
        (
            "ck_derived_features_distance_nonnegative",
            "derived_features",
            "distance >= 0",
        ),
        (
            "ck_derived_features_month_max_nonnegative",
            "derived_features",
            "one_month_max_amount >= 0",
        ),
        (
            "ck_derived_features_month_std_nonnegative",
            "derived_features",
            "one_month_std_dev >= 0",
        ),
        (
            "ck_derived_features_dawn_max_nonnegative",
            "derived_features",
            "dawn_one_month_max_amount >= 0",
        ),
        (
            "ck_derived_features_dawn_std_nonnegative",
            "derived_features",
            "dawn_one_month_std_dev >= 0",
        ),
        (
            "ck_derived_features_recent_count_nonnegative",
            "derived_features",
            "number_of_transaction_with_the_account >= 0",
        ),
        (
            "ck_derived_features_history_count_nonnegative",
            "derived_features",
            "transaction_history_with_the_account >= 0",
        ),
    )
    for name, table, condition in constraints:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for name, table in (
        ("ck_derived_features_history_count_nonnegative", "derived_features"),
        ("ck_derived_features_recent_count_nonnegative", "derived_features"),
        ("ck_derived_features_dawn_std_nonnegative", "derived_features"),
        ("ck_derived_features_dawn_max_nonnegative", "derived_features"),
        ("ck_derived_features_month_std_nonnegative", "derived_features"),
        ("ck_derived_features_month_max_nonnegative", "derived_features"),
        ("ck_derived_features_distance_nonnegative", "derived_features"),
        ("ck_accounts_remaining_daily_limit_nonnegative", "accounts"),
        ("ck_accounts_amount_daily_limit_nonnegative", "accounts"),
        ("ck_transactions_remaining_daily_limit_nonnegative", "transactions"),
        ("ck_transactions_initial_balance_nonnegative", "transactions"),
        ("ck_transactions_transaction_amount_positive", "transactions"),
    ):
        op.drop_constraint(name, table, type_="check")

    op.execute(
        "UPDATE transactions SET operating_system = 'others' "
        "WHERE operating_system IS NULL"
    )
    op.alter_column(
        "transactions",
        "operating_system",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    # raw60이 허용하지만 기존 계약이 모르는 값은 downgrade 전에 안전한 기본값으로
    # 바꿔 폐쇄형 제약을 다시 만들 수 있게 한다.
    op.execute(
        "UPDATE transactions SET error_code = 'a' "
        "WHERE error_code NOT IN ('a', 'b', 'c', 'd', 'e', 'f')"
    )
    op.alter_column(
        "transactions",
        "error_code",
        type_=sa.String(length=8),
        existing_type=sa.String(length=64),
    )
    op.create_check_constraint(
        "ck_transactions_error_code",
        "transactions",
        "error_code IN ('a', 'b', 'c', 'd', 'e', 'f')",
    )

    op.drop_constraint("ck_transactions_channel", "transactions", type_="check")
    op.execute(
        "UPDATE transactions SET channel = CASE channel "
        "WHEN 'atm' THEN 'ATM' WHEN 'others' THEN 'Others' ELSE channel END"
    )
    op.create_check_constraint(
        "ck_transactions_channel",
        "transactions",
        "channel IN ('mobile', 'internet', 'ATM', 'Others')",
    )

    op.add_column("customers", sa.Column("birthyear", sa.SmallInteger(), nullable=True))
    op.execute(
        "UPDATE customers SET birthyear = EXTRACT(YEAR FROM birth_date)::smallint"
    )
    op.alter_column("customers", "birthyear", nullable=False)
    op.drop_column("customers", "birth_date")
