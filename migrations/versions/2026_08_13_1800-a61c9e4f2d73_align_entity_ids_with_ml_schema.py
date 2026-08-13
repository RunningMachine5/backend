"""align entity identifiers with the final schema

확정된 엔티티 계약을 실제 PostgreSQL 스키마에 반영한다.

* customers/account/customer_events/transactions/derived_features의 PK 이름 정리
* 거래·이벤트의 계좌 참조를 내부 ID 대신 account_number로 전환
* 거래 nullable·길이 계약과 account_type ``e`` 지원 반영
* 고객 이벤트 코드와 입금 플래그의 최종 이름 반영

Revision ID: a61c9e4f2d73
Revises: 8f4d2c6a1b90
Create Date: 2026-08-13 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a61c9e4f2d73"
down_revision: str | Sequence[str] | None = "8f4d2c6a1b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_legacy_account_references()
    _rename_entity_identifiers()
    _convert_account_references_to_numbers()
    _align_transaction_contract()
    _align_event_codes()
    _create_final_constraints_and_indexes()


def _drop_legacy_account_references() -> None:
    op.drop_constraint(
        "transactions_source_account_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "transactions_recipient_account_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "customer_events_account_id_fkey",
        "customer_events",
        type_="foreignkey",
    )
    op.drop_index("ix_transactions_source_account_id", table_name="transactions")
    op.drop_index("ix_transactions_recipient_account_id", table_name="transactions")
    op.drop_index("ix_customer_events_account_id", table_name="customer_events")
    op.drop_constraint("ck_accounts_account_type", "accounts", type_="check")


def _rename_entity_identifiers() -> None:
    # PostgreSQL은 참조 대상 컬럼 rename을 기존 FK 정의에도 자동 반영한다.
    # 따라서 transaction_id를 유지하는 자식 테이블의 FK는 별도 재생성이 없다.
    op.alter_column(
        "customers",
        "customer_id",
        new_column_name="id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "customers",
        "personal_identifier",
        new_column_name="name",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "accounts",
        "account_id",
        new_column_name="id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "transaction_id",
        new_column_name="id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "customer_events",
        "event_id",
        new_column_name="id",
        existing_type=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "derived_features",
        "transaction_id",
        new_column_name="id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "derived_features",
        "flag_deposit_more_than_tenmillion",
        new_column_name="flag_deposit_more_than_tenMillion",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def _convert_account_references_to_numbers() -> None:
    op.alter_column(
        "transactions",
        "source_account_id",
        new_column_name="source_account_number",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "recipient_account_id",
        new_column_name="recipient_account_number",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "customer_events",
        "account_id",
        new_column_name="account_number",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=True,
    )

    # FK로 보장되던 기존 내부 ID를 같은 계좌의 자연키로 치환한다.
    op.execute(
        """
        UPDATE transactions AS t
        SET source_account_number = a.account_number
        FROM accounts AS a
        WHERE t.source_account_number = a.id
        """
    )
    op.execute(
        """
        UPDATE transactions AS t
        SET recipient_account_number = a.account_number
        FROM accounts AS a
        WHERE t.recipient_account_number = a.id
        """
    )
    op.execute(
        """
        UPDATE customer_events AS e
        SET account_number = a.account_number
        FROM accounts AS a
        WHERE e.account_number = a.id
        """
    )


def _align_transaction_contract() -> None:
    # varchar 축소가 기존 테스트 데이터를 이유로 배포를 막지 않도록 보존 가능한
    # 앞 8자를 남긴다. 신규 요청은 애플리케이션 계약에서 최대 8자로 제한된다.
    op.execute(
        "UPDATE transactions SET error_code = left(error_code, 8) "
        "WHERE length(error_code) > 8"
    )
    op.alter_column(
        "transactions",
        "error_code",
        type_=sa.String(length=8),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    for column in (
        "access_medium",
        "initial_balance",
        "balance",
        "remaining_amount_daily_limit_exceeded",
    ):
        existing_type = (
            sa.String(length=8) if column == "access_medium" else sa.BigInteger()
        )
        op.alter_column(
            "transactions",
            column,
            existing_type=existing_type,
            nullable=True,
        )


def _align_event_codes() -> None:
    replacements = {
        "AUTH_1": "OFFICIAL_CERTIFICATION",
        "AUTH_2": "PRIVATE_CERTIFICATION",
        "AUTH_3": "SECURITY_CARD_OTP",
        "PRIVACY": "PRIVACY_MODIFICATION",
    }
    for old, new in replacements.items():
        op.execute(
            sa.text(
                "UPDATE customer_events SET event_type = :new "
                "WHERE event_type = :old"
            ).bindparams(old=old, new=new)
        )


def _create_final_constraints_and_indexes() -> None:
    op.create_foreign_key(
        "fk_transactions_source_account_number_accounts",
        "transactions",
        "accounts",
        ["source_account_number"],
        ["account_number"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_transactions_recipient_account_number_accounts",
        "transactions",
        "accounts",
        ["recipient_account_number"],
        ["account_number"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_customer_events_account_number_accounts",
        "customer_events",
        "accounts",
        ["account_number"],
        ["account_number"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transactions_source_account_number",
        "transactions",
        ["source_account_number"],
    )
    op.create_index(
        "ix_transactions_recipient_account_number",
        "transactions",
        ["recipient_account_number"],
    )
    op.create_index(
        "ix_customer_events_account_number",
        "customer_events",
        ["account_number"],
    )
    op.create_check_constraint(
        "ck_accounts_account_type",
        "accounts",
        "account_type IS NULL OR account_type IN ('a','b','c','d','e')",
    )


def downgrade() -> None:
    _drop_final_constraints_and_indexes()
    _restore_legacy_transaction_contract()
    _restore_legacy_event_codes()
    _convert_account_references_to_ids()
    _restore_legacy_identifier_names()
    _create_legacy_constraints_and_indexes()


def _drop_final_constraints_and_indexes() -> None:
    op.drop_constraint("ck_accounts_account_type", "accounts", type_="check")
    op.drop_constraint(
        "fk_customer_events_account_number_accounts",
        "customer_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_transactions_recipient_account_number_accounts",
        "transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_transactions_source_account_number_accounts",
        "transactions",
        type_="foreignkey",
    )
    op.drop_index("ix_customer_events_account_number", table_name="customer_events")
    op.drop_index(
        "ix_transactions_recipient_account_number", table_name="transactions"
    )
    op.drop_index("ix_transactions_source_account_number", table_name="transactions")


def _restore_legacy_transaction_contract() -> None:
    op.execute(
        "UPDATE transactions SET access_medium = 'h' WHERE access_medium IS NULL"
    )
    for column in (
        "initial_balance",
        "balance",
        "remaining_amount_daily_limit_exceeded",
    ):
        op.execute(f"UPDATE transactions SET {column} = 0 WHERE {column} IS NULL")

    op.alter_column(
        "transactions",
        "access_medium",
        existing_type=sa.String(length=8),
        nullable=False,
    )
    for column in (
        "initial_balance",
        "balance",
        "remaining_amount_daily_limit_exceeded",
    ):
        op.alter_column(
            "transactions",
            column,
            existing_type=sa.BigInteger(),
            nullable=False,
        )
    op.alter_column(
        "transactions",
        "error_code",
        type_=sa.String(length=64),
        existing_type=sa.String(length=8),
        existing_nullable=False,
    )
    op.execute("UPDATE accounts SET account_type = 'd' WHERE account_type = 'e'")


def _restore_legacy_event_codes() -> None:
    replacements = {
        "OFFICIAL_CERTIFICATION": "AUTH_1",
        "PRIVATE_CERTIFICATION": "AUTH_2",
        "SECURITY_CARD_OTP": "AUTH_3",
        "PRIVACY_MODIFICATION": "PRIVACY",
    }
    for new, old in replacements.items():
        op.execute(
            sa.text(
                "UPDATE customer_events SET event_type = :old "
                "WHERE event_type = :new"
            ).bindparams(old=old, new=new)
        )


def _convert_account_references_to_ids() -> None:
    op.execute(
        """
        UPDATE transactions AS t
        SET source_account_number = a.id
        FROM accounts AS a
        WHERE t.source_account_number = a.account_number
        """
    )
    op.execute(
        """
        UPDATE transactions AS t
        SET recipient_account_number = a.id
        FROM accounts AS a
        WHERE t.recipient_account_number = a.account_number
        """
    )
    op.execute(
        """
        UPDATE customer_events AS e
        SET account_number = a.id
        FROM accounts AS a
        WHERE e.account_number = a.account_number
        """
    )

    op.alter_column(
        "transactions",
        "source_account_number",
        new_column_name="source_account_id",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "recipient_account_number",
        new_column_name="recipient_account_id",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "customer_events",
        "account_number",
        new_column_name="account_id",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )


def _restore_legacy_identifier_names() -> None:
    op.alter_column(
        "derived_features",
        "flag_deposit_more_than_tenMillion",
        new_column_name="flag_deposit_more_than_tenmillion",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "derived_features",
        "id",
        new_column_name="transaction_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "customer_events",
        "id",
        new_column_name="event_id",
        existing_type=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "transactions",
        "id",
        new_column_name="transaction_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "accounts",
        "id",
        new_column_name="account_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "customers",
        "name",
        new_column_name="personal_identifier",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "customers",
        "id",
        new_column_name="customer_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )


def _create_legacy_constraints_and_indexes() -> None:
    op.create_foreign_key(
        "transactions_source_account_id_fkey",
        "transactions",
        "accounts",
        ["source_account_id"],
        ["account_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "transactions_recipient_account_id_fkey",
        "transactions",
        "accounts",
        ["recipient_account_id"],
        ["account_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "customer_events_account_id_fkey",
        "customer_events",
        "accounts",
        ["account_id"],
        ["account_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transactions_source_account_id",
        "transactions",
        ["source_account_id"],
    )
    op.create_index(
        "ix_transactions_recipient_account_id",
        "transactions",
        ["recipient_account_id"],
    )
    op.create_index(
        "ix_customer_events_account_id", "customer_events", ["account_id"]
    )
    op.create_check_constraint(
        "ck_accounts_account_type",
        "accounts",
        "account_type IS NULL OR account_type IN ('a','b','c','d')",
    )
