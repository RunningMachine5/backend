"""split chat phase and remove chatbot rescoring

Revision ID: a81d4e2f6c90
Revises: 6f8b7d9c2a41
Create Date: 2026-08-21 19:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a81d4e2f6c90"
down_revision: Union[str, Sequence[str], None] = "6f8b7d9c2a41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSON_COLUMN = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)
BIGINT_PRIMARY_KEY = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("conversation_phase", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("discrimination_question_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("ownership_answer", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("confirmed_fraud_type", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_chat_sessions_conversation_phase",
        "chat_sessions",
        "conversation_phase IS NULL OR conversation_phase IN "
        "('DISCRIMINATION', 'FREE_CHAT', 'HANDOFF_PENDING', 'NORMAL_GUIDE')",
    )
    op.create_check_constraint(
        "ck_chat_sessions_discrimination_question_id",
        "chat_sessions",
        "discrimination_question_id IS NULL OR discrimination_question_id IN "
        "('OWNERSHIP', 'PRIMARY_CHECK', 'SECONDARY_CHECK')",
    )
    op.create_check_constraint(
        "ck_chat_sessions_ownership_answer",
        "chat_sessions",
        "ownership_answer IS NULL OR ownership_answer IN ('ANSWER_YES', 'ANSWER_NO')",
    )

    op.create_table(
        "chat_discrimination_actions",
        sa.Column("action_id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("response_payload", JSON_COLUMN, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "question_id IN ('OWNERSHIP', 'PRIMARY_CHECK', 'SECONDARY_CHECK')",
            name="ck_chat_discrimination_actions_question_id",
        ),
        sa.CheckConstraint(
            "action IN ('ANSWER_YES', 'ANSWER_NO')",
            name="ck_chat_discrimination_actions_action",
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "request_id",
            name="uq_chat_discrimination_actions_session_request",
        ),
    )

    op.drop_table("fraud_type_score_after_chat")
    op.drop_table("chat_fraud_circumstances")


def downgrade() -> None:
    op.create_table(
        "chat_fraud_circumstances",
        sa.Column("circumstance_id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("circumstance_code", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_answer_id", BIGINT_PRIMARY_KEY, nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_answer_id"], ["chat_answers.answer_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("circumstance_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "circumstance_code",
            name="uq_chat_fraud_circumstances_session_code",
        ),
    )
    op.create_table(
        "fraud_type_score_after_chat",
        sa.Column("transaction_id", BIGINT_PRIMARY_KEY, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("type_scores", JSON_COLUMN, nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )

    op.drop_table("chat_discrimination_actions")
    op.drop_constraint(
        "ck_chat_sessions_ownership_answer", "chat_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_chat_sessions_discrimination_question_id",
        "chat_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_chat_sessions_conversation_phase", "chat_sessions", type_="check"
    )
    op.drop_column("chat_sessions", "confirmed_fraud_type")
    op.drop_column("chat_sessions", "ownership_answer")
    op.drop_column("chat_sessions", "discrimination_question_id")
    op.drop_column("chat_sessions", "conversation_phase")
