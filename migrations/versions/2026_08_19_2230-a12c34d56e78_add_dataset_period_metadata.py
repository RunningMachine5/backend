"""add dataset period metadata

Revision ID: a12c34d56e78
Revises: 6040d304df8d
Create Date: 2026-08-19 22:30:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a12c34d56e78"
down_revision: Union[str, Sequence[str], None] = "6040d304df8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dataset_versions", sa.Column("period_start", sa.Date()))
    op.add_column("dataset_versions", sa.Column("period_end", sa.Date()))
    op.add_column(
        "dataset_versions",
        sa.Column(
            "period_normal_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "period_fraud_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "period_fraud_count")
    op.drop_column("dataset_versions", "period_normal_count")
    op.drop_column("dataset_versions", "period_end")
    op.drop_column("dataset_versions", "period_start")
