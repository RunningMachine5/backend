"""add chatbot conversation schema

고객 챗봇의 질문·답변 이력과 고객 행동/사기 정황 추출 결과를 추가하고,
채팅 후 사기유형 점수를 네 유형의 JSON 점수 맵으로 전환한다.

Revision ID: c4f7a2b9d810
Revises: a61c9e4f2d73
Create Date: 2026-08-13 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c4f7a2b9d810"
down_revision: str | Sequence[str] | None = "a61c9e4f2d73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FRAUD_TYPE_CODES = (
    "voice_phishing",
    "messenger_phishing",
    "account_takeover",
    "fraud_used_account",
)
_FRAUD_TYPE_SQL_VALUES = ", ".join(
    f"'{fraud_type}'" for fraud_type in _FRAUD_TYPE_CODES
)


def upgrade() -> None:
    _upgrade_chat_sessions()
    _create_chat_answers()
    _create_extraction_tables()
    _upgrade_after_chat_scores()


def downgrade() -> None:
    _downgrade_after_chat_scores()
    _drop_extraction_tables()
    op.drop_table("agent_chat_answers")
    _downgrade_chat_sessions()


def _upgrade_chat_sessions() -> None:
    op.add_column(
        "agent_chat_sessions",
        sa.Column(
            "question_step",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_chat_sessions",
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_chat_sessions",
        sa.Column("notified_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_chat_sessions",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("agent_chat_sessions", "top_fraud_types")


def _downgrade_chat_sessions() -> None:
    op.add_column(
        "agent_chat_sessions",
        sa.Column(
            "top_fraud_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.drop_column("agent_chat_sessions", "completed_at")
    op.drop_column("agent_chat_sessions", "notified_email")
    op.drop_column("agent_chat_sessions", "email_sent_at")
    op.drop_column("agent_chat_sessions", "question_step")


def _create_chat_answers() -> None:
    op.create_table(
        "agent_chat_answers",
        sa.Column("answer_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("question_step", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("quality_verdict", sa.String(length=16), nullable=True),
        sa.Column("verdict_skip_reason", sa.String(length=24), nullable=True),
        sa.Column(
            "is_adopted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_no BETWEEN 1 AND 3",
            name="ck_agent_chat_answers_attempt_no",
        ),
        sa.CheckConstraint(
            "quality_verdict IS NULL OR quality_verdict IN "
            "('SUFFICIENT', 'TOO_VAGUE', 'NON_ANSWER', 'REFUSAL', 'WANT_END')",
            name="ck_agent_chat_answers_quality_verdict",
        ),
        sa.CheckConstraint(
            "verdict_skip_reason IS NULL OR verdict_skip_reason IN "
            "('MAX_RETRY_EXCEEDED', 'EVALUATOR_FAILED')",
            name="ck_agent_chat_answers_verdict_skip_reason",
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["agent_chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_chat_messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("answer_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "question_step",
            "attempt_no",
            name="uq_agent_chat_answers_session_step_attempt",
        ),
        sa.UniqueConstraint(
            "message_id",
            name="uq_agent_chat_answers_message_id",
        ),
    )
    op.create_index(
        "ix_agent_chat_answers_session_step",
        "agent_chat_answers",
        ["chat_session_id", "question_step"],
    )
    # 질문당 채택 답변은 최대 하나다. 부분 조건은 autogenerate에 맡기지 않는다.
    op.create_index(
        "uq_agent_chat_answers_adopted",
        "agent_chat_answers",
        ["chat_session_id", "question_step"],
        unique=True,
        postgresql_where=sa.text("is_adopted"),
    )


def _create_extraction_tables() -> None:
    op.create_table(
        "agent_chat_customer_actions",
        sa.Column("action_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_verified", sa.Boolean(), nullable=False),
        sa.Column("source_answer_id", sa.BigInteger(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["agent_chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_answer_id"],
            ["agent_chat_answers.answer_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "action_code",
            name="uq_agent_chat_customer_actions_session_code",
        ),
    )
    op.create_table(
        "agent_chat_fraud_circumstances",
        sa.Column(
            "circumstance_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("circumstance_code", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_verified", sa.Boolean(), nullable=False),
        sa.Column("source_answer_id", sa.BigInteger(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["agent_chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_answer_id"],
            ["agent_chat_answers.answer_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("circumstance_id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "circumstance_code",
            name="uq_agent_chat_fraud_circumstances_session_code",
        ),
    )


def _drop_extraction_tables() -> None:
    op.drop_table("agent_chat_fraud_circumstances")
    op.drop_table("agent_chat_customer_actions")


def _upgrade_after_chat_scores() -> None:
    connection = op.get_bind()
    unknown_types = connection.execute(
        sa.text(
            "SELECT DISTINCT primary_fraud_type "
            "FROM fraud_type_score_after_chat "
            "WHERE primary_fraud_type IS NOT NULL "
            f"AND primary_fraud_type NOT IN ({_FRAUD_TYPE_SQL_VALUES})"
        )
    ).scalars().all()
    if unknown_types:
        raise RuntimeError(
            "fraud_type_score_after_chat에 허용되지 않은 대표 유형이 있습니다: "
            f"{sorted(unknown_types)}"
        )

    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column("chat_session_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column(
            "type_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.execute(
        """
        UPDATE fraud_type_score_after_chat AS scores
        SET chat_session_id = sessions.chat_session_id
        FROM agent_chat_sessions AS sessions
        WHERE sessions.transaction_id = scores.transaction_id
        """
    )
    missing_sessions = connection.execute(
        sa.text(
            "SELECT transaction_id FROM fraud_type_score_after_chat "
            "WHERE chat_session_id IS NULL ORDER BY transaction_id"
        )
    ).scalars().all()
    if missing_sessions:
        raise RuntimeError(
            "채팅 세션을 찾을 수 없는 사기유형 점수 행이 있습니다: "
            f"{missing_sessions}"
        )

    op.execute(
        """
        UPDATE fraud_type_score_after_chat
        SET type_scores = CASE
            WHEN primary_fraud_type IS NULL THEN
                jsonb_build_object(
                    'voice_phishing', 0,
                    'messenger_phishing', 0,
                    'account_takeover', 0,
                    'fraud_used_account', 0
                )
            ELSE
                jsonb_set(
                    jsonb_build_object(
                        'voice_phishing', 0,
                        'messenger_phishing', 0,
                        'account_takeover', 0,
                        'fraud_used_account', 0
                    ),
                    ARRAY[primary_fraud_type],
                    to_jsonb(COALESCE(primary_fraud_type_score, 0.0)),
                    true
                )
        END
        """
    )
    invalid_scores = connection.execute(
        sa.text(
            """
            SELECT transaction_id
            FROM fraud_type_score_after_chat
            WHERE jsonb_typeof(type_scores) <> 'object'
               OR NOT type_scores ?& ARRAY[
                    'voice_phishing',
                    'messenger_phishing',
                    'account_takeover',
                    'fraud_used_account'
               ]
               OR type_scores - ARRAY[
                    'voice_phishing',
                    'messenger_phishing',
                    'account_takeover',
                    'fraud_used_account'
               ] <> '{}'::jsonb
            ORDER BY transaction_id
            """
        )
    ).scalars().all()
    if invalid_scores:
        raise RuntimeError(
            "사기유형별 점수 백필 검증에 실패했습니다: "
            f"{invalid_scores}"
        )

    op.alter_column("fraud_type_score_after_chat", "chat_session_id", nullable=False)
    op.alter_column("fraud_type_score_after_chat", "type_scores", nullable=False)
    op.create_foreign_key(
        "fk_fraud_type_score_after_chat_chat_session_id",
        "fraud_type_score_after_chat",
        "agent_chat_sessions",
        ["chat_session_id"],
        ["chat_session_id"],
        ondelete="CASCADE",
    )
    op.drop_column("fraud_type_score_after_chat", "primary_fraud_type_score")
    op.drop_column("fraud_type_score_after_chat", "primary_fraud_type")


def _downgrade_after_chat_scores() -> None:
    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column("primary_fraud_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column("primary_fraud_type_score", sa.Float(), nullable=True),
    )
    # 동점과 모든 유형이 0점인 경우에는 기존 nullable 대표 유형을 복원하지 않는다.
    op.execute(
        """
        WITH expanded AS (
            SELECT
                scores.transaction_id,
                item.key AS fraud_type,
                item.value::double precision AS score
            FROM fraud_type_score_after_chat AS scores
            CROSS JOIN LATERAL jsonb_each_text(scores.type_scores) AS item
        ),
        maxima AS (
            SELECT transaction_id, max(score) AS max_score
            FROM expanded
            GROUP BY transaction_id
        ),
        decided AS (
            SELECT
                expanded.transaction_id,
                max(expanded.fraud_type) AS fraud_type,
                maxima.max_score
            FROM expanded
            JOIN maxima USING (transaction_id)
            WHERE expanded.score = maxima.max_score
              AND maxima.max_score > 0
            GROUP BY expanded.transaction_id, maxima.max_score
            HAVING count(*) = 1
        )
        UPDATE fraud_type_score_after_chat AS scores
        SET primary_fraud_type = decided.fraud_type,
            primary_fraud_type_score = decided.max_score
        FROM decided
        WHERE decided.transaction_id = scores.transaction_id
        """
    )
    op.drop_constraint(
        "fk_fraud_type_score_after_chat_chat_session_id",
        "fraud_type_score_after_chat",
        type_="foreignkey",
    )
    op.drop_column("fraud_type_score_after_chat", "type_scores")
    op.drop_column("fraud_type_score_after_chat", "chat_session_id")
