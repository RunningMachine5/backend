"""replace customer actions with guide search queries

고객행동 19종 패턴 저장을 제거하고 답변별 동적 검색 단위를 저장한다.
기존 고객행동 행은 백필하지 않는다.

Revision ID: f8a1b2c3d4e5
Revises: d5e8f1a2b3c4, e7b4c9d1a620
Create Date: 2026-08-15 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f8a1b2c3d4e5"
down_revision: str | Sequence[str] | None = (
    "d5e8f1a2b3c4",
    "e7b4c9d1a620",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("chat_customer_actions")
    op.create_table(
        "chat_guide_search_queries",
        sa.Column(
            "guide_search_query_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_answer_id", sa.BigInteger(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 5",
            name="ck_chat_guide_search_queries_position",
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_answer_id"],
            ["chat_answers.answer_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("guide_search_query_id"),
        sa.UniqueConstraint(
            "source_answer_id",
            "position",
            name="uq_chat_guide_search_queries_answer_position",
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_guide_search_queries")
    op.create_table(
        "chat_customer_actions",
        sa.Column("action_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_answer_id", sa.BigInteger(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_answer_id"],
            ["chat_answers.answer_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "action_code",
            name="uq_chat_customer_actions_session_code",
        ),
    )
