"""add chat session top fraud type scores

Revision ID: 6f8b7d9c2a41
Revises: 31268baf1afc
Create Date: 2026-08-21 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "6f8b7d9c2a41"
down_revision: Union[str, Sequence[str], None] = "31268baf1afc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "chat_sessions",
        sa.Column(
            "top_fraud_type_scores",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()),
                "postgresql",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("chat_sessions", "top_fraud_type_scores")
