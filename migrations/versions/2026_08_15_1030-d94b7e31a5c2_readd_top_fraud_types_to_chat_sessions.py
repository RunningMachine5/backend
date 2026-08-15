"""readd top fraud types to chat sessions

유형판별 질문(PRD 2.4) 도입으로 사용처가 생긴 top_fraud_types 컬럼을
chat_sessions에 재추가한다. c4f7a2b9d810이 삭제한 컬럼과 같은 형태이며,
룰 채점 점수 내림차순 상위 2개 사기유형 코드를 저장한다. NULL이면
유형판별 질문 대신 일반 질문 폴백을 쓴다.

Revision ID: d94b7e31a5c2
Revises: c4f7a2b9d810
Create Date: 2026-08-15 10:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d94b7e31a5c2"
down_revision: Union[str, Sequence[str], None] = "c4f7a2b9d810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "top_fraud_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "top_fraud_types")
