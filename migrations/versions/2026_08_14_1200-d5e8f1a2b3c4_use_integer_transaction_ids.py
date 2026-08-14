"""use integer transaction ids and prediction result names

거래 PK와 모든 직접 자식 FK를 BIGINT로 통일하고 ML 응답 컬럼명을
``predict_result``/``predict_proba``로 맞춘다. 기존 문자열 거래 ID는 거래시각,
생성시각, 기존 ID 순으로 1부터 다시 부여하고 모든 자식 FK를 같은 매핑으로
변환해 기존 거래·예측·룰·Agent·챗봇 데이터를 보존한다.

Revision ID: d5e8f1a2b3c4
Revises: c4f7a2b9d810
Create Date: 2026-08-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8f1a2b3c4"
down_revision: str | Sequence[str] | None = "c4f7a2b9d810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRANSACTION_REFERENCES = (
    ("derived_features", "id", "derived_features_id_fkey"),
    ("transaction_labels", "transaction_id", "transaction_labels_transaction_id_fkey"),
    (
        "ml_prediction_results",
        "transaction_id",
        "ml_prediction_results_transaction_id_fkey",
    ),
    (
        "fraud_type_score_results",
        "transaction_id",
        "fraud_type_score_results_transaction_id_fkey",
    ),
    ("agent_cases", "transaction_id", "agent_cases_transaction_id_fkey"),
    ("chat_sessions", "transaction_id", "chat_sessions_transaction_id_fkey"),
    (
        "fraud_type_score_after_chat",
        "transaction_id",
        "fraud_type_score_after_chat_transaction_id_fkey",
    ),
)


def upgrade() -> None:
    _drop_transaction_foreign_keys()
    _renumber_legacy_transaction_ids()

    # 최신 거래 요청은 ATM·지점 거래에서 고객 ID를 보내지 않을 수 있고,
    # 입출금 부호를 원본 그대로 보존한다.
    op.alter_column(
        "transactions",
        "customer_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.execute(
        "ALTER TABLE transactions DROP CONSTRAINT IF EXISTS "
        "ck_transactions_transaction_amount_positive"
    )
    op.execute(
        "ALTER TABLE transactions DROP CONSTRAINT IF EXISTS ck_transactions_error_code"
    )
    op.alter_column(
        "transactions",
        "error_code",
        existing_type=sa.String(length=8),
        nullable=True,
    )

    op.alter_column(
        "transactions",
        "id",
        existing_type=sa.String(length=128),
        type_=sa.BigInteger(),
        existing_nullable=False,
        postgresql_using="replace(id, '__int__:', '')::bigint",
    )
    for table_name, column_name, _ in _TRANSACTION_REFERENCES:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.String(length=128),
            type_=sa.BigInteger(),
            existing_nullable=False,
            postgresql_using=(f"replace({column_name}, '__int__:', '')::bigint"),
        )

    op.execute("CREATE SEQUENCE transactions_id_seq OWNED BY transactions.id")
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN id "
        "SET DEFAULT nextval('transactions_id_seq')"
    )
    op.execute(
        "SELECT setval("
        "'transactions_id_seq', "
        "COALESCE((SELECT max(id) FROM transactions), 1), "
        "EXISTS (SELECT 1 FROM transactions))"
    )

    op.alter_column(
        "ml_prediction_results",
        "prediction_is_fraud",
        new_column_name="predict_result",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "ml_prediction_results",
        "fraud_probability",
        new_column_name="predict_proba",
        existing_type=sa.Float(),
        existing_nullable=False,
    )
    _create_transaction_foreign_keys()


def downgrade() -> None:
    _drop_transaction_foreign_keys()

    # 구형 계약은 오류 코드가 필수였으므로 새 요청에서 비어 있던 값만
    # 허용 범주의 기본 코드로 되돌린 뒤 NOT NULL 제약을 복원한다.
    op.execute("UPDATE transactions SET error_code = 'a' WHERE error_code IS NULL")
    op.alter_column(
        "transactions",
        "error_code",
        existing_type=sa.String(length=8),
        nullable=False,
    )
    op.alter_column(
        "transactions",
        "customer_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_transactions_transaction_amount_positive",
        "transactions",
        "transaction_amount > 0",
    )
    op.create_check_constraint(
        "ck_transactions_error_code",
        "transactions",
        "error_code IN ('a', 'b', 'c', 'd', 'e', 'f')",
    )

    op.execute("ALTER TABLE transactions ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS transactions_id_seq")
    op.alter_column(
        "transactions",
        "id",
        existing_type=sa.BigInteger(),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="id::varchar(64)",
    )
    for table_name, column_name, _ in _TRANSACTION_REFERENCES:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.BigInteger(),
            type_=sa.String(length=64),
            existing_nullable=False,
            postgresql_using=f"{column_name}::varchar(64)",
        )

    op.alter_column(
        "ml_prediction_results",
        "predict_result",
        new_column_name="prediction_is_fraud",
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.alter_column(
        "ml_prediction_results",
        "predict_proba",
        new_column_name="fraud_probability",
        existing_type=sa.Float(),
        existing_nullable=False,
    )
    _create_transaction_foreign_keys()


def _renumber_legacy_transaction_ids() -> None:
    """기존 문자열 ID를 결정적인 연속 정수 ID로 모든 참조와 함께 바꾼다."""

    op.execute(
        """
        CREATE TEMP TABLE transaction_id_map ON COMMIT DROP AS
        SELECT
            id AS old_id,
            row_number() OVER (
                ORDER BY transaction_datetime, created_at, id
            )::bigint AS new_id
        FROM transactions
        """
    )
    op.execute(
        "ALTER TABLE transaction_id_map ADD PRIMARY KEY (old_id), ADD UNIQUE (new_id)"
    )

    # ID 교환 충돌을 피하려고 먼저 모든 값을 임시 prefix 공간으로 이동한다.
    op.alter_column(
        "transactions",
        "id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    for table_name, column_name, _ in _TRANSACTION_REFERENCES:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
        op.execute(
            f"UPDATE {table_name} SET {column_name} = '__legacy__:' || {column_name}"
        )
    op.execute("UPDATE transactions SET id = '__legacy__:' || id")

    op.execute(
        """
        UPDATE transactions AS target
        SET id = '__int__:' || mapping.new_id::text
        FROM transaction_id_map AS mapping
        WHERE target.id = '__legacy__:' || mapping.old_id
        """
    )
    for table_name, column_name, _ in _TRANSACTION_REFERENCES:
        op.execute(
            f"""
            UPDATE {table_name} AS target
            SET {column_name} = '__int__:' || mapping.new_id::text
            FROM transaction_id_map AS mapping
            WHERE target.{column_name} = '__legacy__:' || mapping.old_id
            """
        )


def _drop_transaction_foreign_keys() -> None:
    """현재 이름과 무관하게 transactions를 참조하는 FK만 제거한다."""

    op.execute(
        """
        DO $$
        DECLARE fk record;
        BEGIN
            FOR fk IN
                SELECT conrelid::regclass::text AS table_name, conname
                FROM pg_constraint
                WHERE contype = 'f'
                  AND confrelid = 'transactions'::regclass
            LOOP
                EXECUTE format(
                    'ALTER TABLE %s DROP CONSTRAINT %I',
                    fk.table_name,
                    fk.conname
                );
            END LOOP;
        END
        $$
        """
    )


def _create_transaction_foreign_keys() -> None:
    for table_name, column_name, constraint_name in _TRANSACTION_REFERENCES:
        op.create_foreign_key(
            constraint_name,
            table_name,
            "transactions",
            [column_name],
            ["id"],
            ondelete="CASCADE",
        )
