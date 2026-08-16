"""remove evidence verified from chatbot extractions

검증에 실패한 evidence는 저장하지 않는 정책으로 변경해 감사용 boolean 컬럼을 제거한다.

Revision ID: e7b4c9d1a620
Revises: d94b7e31a5c2
Create Date: 2026-08-15 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7b4c9d1a620"
down_revision: Union[str, Sequence[str], None] = "d94b7e31a5c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("chat_customer_actions", "evidence_verified")
    op.drop_column("chat_fraud_circumstances", "evidence_verified")


def downgrade() -> None:
    op.add_column(
        "chat_customer_actions",
        sa.Column(
            "evidence_verified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "chat_fraud_circumstances",
        sa.Column(
            "evidence_verified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.alter_column(
        "chat_customer_actions",
        "evidence_verified",
        server_default=None,
    )
    op.alter_column(
        "chat_fraud_circumstances",
        "evidence_verified",
        server_default=None,
    )
