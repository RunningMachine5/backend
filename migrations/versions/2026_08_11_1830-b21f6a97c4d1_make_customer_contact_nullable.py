"""make customer contact columns nullable

Revision ID: b21f6a97c4d1
Revises: 0a2e98ae82c9
Create Date: 2026-08-11 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b21f6a97c4d1"
down_revision: str | Sequence[str] | None = "0a2e98ae82c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow contacts omitted by the transaction CSV contract."""

    op.alter_column(
        "customers",
        "email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.alter_column(
        "customers",
        "phone_number",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.execute(sa.text("UPDATE customers SET email = NULL WHERE email = ''"))
    op.execute(
        sa.text("UPDATE customers SET phone_number = NULL WHERE phone_number = ''")
    )


def downgrade() -> None:
    """Require both customer contact columns again."""

    op.execute(sa.text("UPDATE customers SET email = '' WHERE email IS NULL"))
    op.execute(
        sa.text("UPDATE customers SET phone_number = '' WHERE phone_number IS NULL")
    )
    op.alter_column(
        "customers",
        "phone_number",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "customers",
        "email",
        existing_type=sa.String(length=255),
        nullable=False,
    )
