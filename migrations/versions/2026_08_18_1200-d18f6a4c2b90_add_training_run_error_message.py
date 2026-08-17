"""add training run error message

Revision ID: d18f6a4c2b90
Revises: c7e4a9b2d105
Create Date: 2026-08-18 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d18f6a4c2b90"
down_revision: str | Sequence[str] | None = "c7e4a9b2d105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the failure reason reported by the Training Job."""

    op.add_column(
        "training_runs",
        sa.Column("error_message", sa.String(length=2000), nullable=True),
    )


def downgrade() -> None:
    """Remove the Training Job failure reason."""

    op.drop_column("training_runs", "error_message")
