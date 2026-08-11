"""add customer_contacts table

Revision ID: 41a44e760c50
Revises: e4b1f9c72a30
Create Date: 2026-08-11 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "41a44e760c50"
down_revision: Union[str, Sequence[str], None] = "e4b1f9c72a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """고객(customer_id) 1:1 이메일·전화번호 연락처 테이블을 생성한다."""

    op.create_table(
        "customer_contacts",
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("customer_id"),
    )


def downgrade() -> None:
    """customer_contacts 테이블을 제거한다."""

    op.drop_table("customer_contacts")
