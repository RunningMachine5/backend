"""drop customer_contacts, add email/phone_number to customers

Revision ID: 0a2e98ae82c9
Revises: 41a44e760c50
Create Date: 2026-08-11 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0a2e98ae82c9"
down_revision: Union[str, Sequence[str], None] = "41a44e760c50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """별도 테이블 대신 customers에 이메일·전화번호 컬럼을 직접 둔다."""

    op.drop_table("customer_contacts")
    op.add_column(
        "customers",
        sa.Column(
            "email", sa.String(length=255), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "phone_number",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )
    op.alter_column("customers", "email", server_default=None)
    op.alter_column("customers", "phone_number", server_default=None)


def downgrade() -> None:
    """customers 컬럼을 제거하고 customer_contacts 테이블을 복원한다."""

    op.drop_column("customers", "phone_number")
    op.drop_column("customers", "email")
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
