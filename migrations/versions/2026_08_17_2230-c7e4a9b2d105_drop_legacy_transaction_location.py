"""align the squashed schema with current transaction models

Revision ID: c7e4a9b2d105
Revises: b4167d7782e1
Create Date: 2026-08-17 22:30:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e4a9b2d105"
down_revision: str | Sequence[str] | None = "b4167d7782e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove legacy fields and align renamed transaction feature columns."""

    op.drop_column("transactions", "location")
    op.alter_column(
        "derived_features",
        "flag_deposit_more_than_tenMillion",
        new_column_name="flag_deposit_more_than_ten_million",
    )
    op.alter_column(
        "derived_features",
        "transaction_resumed_date",
        new_column_name="recipient_transaction_resumed_date",
    )
    op.drop_column("derived_features", "first_time_ios_by_vulnerable_user")

    op.drop_constraint(
        "transactions_recipient_account_number_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.alter_column(
        "transactions",
        "recipient_account_number",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_foreign_key(
        "transactions_recipient_account_number_fkey",
        "transactions",
        "accounts",
        ["recipient_account_number"],
        ["account_number"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Restore the squashed schema fields."""

    op.drop_constraint(
        "transactions_recipient_account_number_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.alter_column(
        "transactions",
        "recipient_account_number",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.create_foreign_key(
        "transactions_recipient_account_number_fkey",
        "transactions",
        "accounts",
        ["recipient_account_number"],
        ["account_number"],
        ondelete="SET NULL",
    )

    op.add_column(
        "derived_features",
        sa.Column(
            "first_time_ios_by_vulnerable_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "derived_features",
        "first_time_ios_by_vulnerable_user",
        server_default=None,
    )
    op.alter_column(
        "derived_features",
        "recipient_transaction_resumed_date",
        new_column_name="transaction_resumed_date",
    )
    op.alter_column(
        "derived_features",
        "flag_deposit_more_than_ten_million",
        new_column_name="flag_deposit_more_than_tenMillion",
    )

    op.add_column(
        "transactions",
        sa.Column("location", sa.Text(), nullable=True),
    )
    op.execute(
        """
        UPDATE transactions
        SET location = CONCAT_WS(' ', location_lat, location_lon)
        """
    )
    op.alter_column("transactions", "location", nullable=False)
