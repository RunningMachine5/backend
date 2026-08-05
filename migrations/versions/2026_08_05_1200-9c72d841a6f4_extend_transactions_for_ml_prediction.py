"""extend transactions for ML prediction

Revision ID: 9c72d841a6f4
Revises: b4391e577668
Create Date: 2026-08-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "9c72d841a6f4"
down_revision: Union[str, Sequence[str], None] = "b4391e577668"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """거래 원본과 ML Stub 응답을 저장할 컬럼을 추가한다."""

    op.add_column(
        "transactions",
        sa.Column(
            "transaction_id",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "prediction_status",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            server_default="RECEIVED",
            nullable=False,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("ml_is_fraud", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("fraud_probability", sa.Float(), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "shap",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "model_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "model_version",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.execute(
        """
        UPDATE transactions
        SET transaction_id = 'LEGACY_' || id::text,
            occurred_at = created_at,
            updated_at = created_at
        """
    )
    op.alter_column("transactions", "transaction_id", nullable=False)
    op.alter_column("transactions", "occurred_at", nullable=False)
    op.alter_column("transactions", "updated_at", nullable=False)
    op.alter_column("transactions", "raw_data", server_default=None)
    op.alter_column("transactions", "prediction_status", server_default=None)
    op.alter_column("transactions", "payment_method", nullable=True)

    op.create_index(
        op.f("ix_transactions_transaction_id"),
        "transactions",
        ["transaction_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_transactions_prediction_status"),
        "transactions",
        ["prediction_status"],
        unique=False,
    )


def downgrade() -> None:
    """ML 연동용 거래 컬럼을 제거한다."""

    op.drop_index(
        op.f("ix_transactions_prediction_status"),
        table_name="transactions",
    )
    op.drop_index(
        op.f("ix_transactions_transaction_id"),
        table_name="transactions",
    )
    op.execute(
        "UPDATE transactions SET payment_method = 'UNKNOWN' "
        "WHERE payment_method IS NULL"
    )
    op.alter_column("transactions", "payment_method", nullable=False)
    op.drop_column("transactions", "updated_at")
    op.drop_column("transactions", "model_version")
    op.drop_column("transactions", "model_name")
    op.drop_column("transactions", "shap")
    op.drop_column("transactions", "fraud_probability")
    op.drop_column("transactions", "ml_is_fraud")
    op.drop_column("transactions", "prediction_status")
    op.drop_column("transactions", "raw_data")
    op.drop_column("transactions", "occurred_at")
    op.drop_column("transactions", "transaction_id")
