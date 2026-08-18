"""merge parallel migration heads

Revision ID: e4a7b2c9d610
Revises: 114319f0f9d6, d18f6a4c2b90
Create Date: 2026-08-18 13:45:00

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e4a7b2c9d610"
down_revision: str | Sequence[str] | None = (
    "114319f0f9d6",
    "d18f6a4c2b90",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge the parallel migration branches without changing the schema."""


def downgrade() -> None:
    """Return to the two parent revisions without changing the schema."""
