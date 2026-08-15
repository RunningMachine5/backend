"""merge migration branches

Revision ID: f3a6c8d2e941
Revises: d5e8f1a2b3c4, e7b4c9d1a620
Create Date: 2026-08-15 16:00:00.000000
"""

from collections.abc import Sequence


revision: str = "f3a6c8d2e941"
down_revision: str | Sequence[str] | None = (
    "d5e8f1a2b3c4",
    "e7b4c9d1a620",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
