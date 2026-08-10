"""allow duplicate customer names

Revision ID: a3f8c1d7e920
Revises: 4e7a9d2c1b60
Create Date: 2026-08-10 09:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a3f8c1d7e920"
down_revision: Union[str, Sequence[str], None] = "4e7a9d2c1b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """고객 이름으로 사용되는 personal_identifier의 unique 제약을 제거한다."""

    op.drop_constraint(
        "uq_customers_personal_identifier",
        "customers",
        type_="unique",
    )


def downgrade() -> None:
    """personal_identifier unique 제약을 복원한다."""

    op.create_unique_constraint(
        "uq_customers_personal_identifier",
        "customers",
        ["personal_identifier"],
    )
