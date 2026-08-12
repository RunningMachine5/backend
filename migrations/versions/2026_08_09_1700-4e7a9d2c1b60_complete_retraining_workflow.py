"""complete the administrator-approved retraining workflow

Revision ID: 4e7a9d2c1b60
Revises: c87fd13a629e
Create Date: 2026-08-09 17:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "4e7a9d2c1b60"
down_revision: Union[str, Sequence[str], None] = "c87fd13a629e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column("split_datetime", sa.DateTime(), nullable=True),
    )

    op.alter_column(
        "training_runs",
        "cloud_run_execution_name",
        new_column_name="cloud_run_operation_name",
        nullable=True,
    )
    op.alter_column("training_runs", "mlflow_run_id", nullable=True)
    op.add_column(
        "training_runs",
        sa.Column("model_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column(
            "comparison_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "training_runs",
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("serving_revision", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("serving_operation_name", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE training_runs SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("training_runs", "updated_at", nullable=False)
    op.add_column(
        "training_runs",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column_name in (
        "decided_at",
        "updated_at",
        "serving_revision",
        "serving_operation_name",
        "decision_reason",
        "comparison_result",
        "model_version",
    ):
        op.drop_column("training_runs", column_name)
    op.alter_column("training_runs", "mlflow_run_id", nullable=False)
    op.alter_column(
        "training_runs",
        "cloud_run_operation_name",
        new_column_name="cloud_run_execution_name",
        nullable=False,
    )
    op.drop_column("dataset_versions", "split_datetime")
