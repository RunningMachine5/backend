"""add top fraud types to chat sessions

agent_chat_sessions에 상위 2개 사기유형 코드(점수 내림차순)를 저장하는
top_fraud_types 컬럼을 추가한다.

Revision ID: 3dad8d92c981
Revises: b21f6a97c4d1
Create Date: 2026-08-12 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "3dad8d92c981"
down_revision: Union[str, Sequence[str], None] = "b21f6a97c4d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_chat_sessions",
        sa.Column(
            "top_fraud_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_chat_sessions", "top_fraud_types")
