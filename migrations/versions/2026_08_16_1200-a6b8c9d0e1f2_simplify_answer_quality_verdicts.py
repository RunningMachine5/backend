"""simplify answer quality verdicts

고객 답변 평가 판정을 SUFFICIENT, TOO_VAGUE, WANT_END 3종으로 축소한다.
기존 NON_ANSWER, REFUSAL 데이터는 없으므로 별도 백필하지 않는다.

Revision ID: a6b8c9d0e1f2
Revises: f3a6c8d2e941, f8a1b2c3d4e5
Create Date: 2026-08-16 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "a6b8c9d0e1f2"
down_revision: str | Sequence[str] | None = (
    "f3a6c8d2e941",
    "f8a1b2c3d4e5",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_chat_answers_quality_verdict",
        "chat_answers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_chat_answers_quality_verdict",
        "chat_answers",
        "quality_verdict IS NULL OR quality_verdict IN "
        "('SUFFICIENT', 'TOO_VAGUE', 'WANT_END')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_chat_answers_quality_verdict",
        "chat_answers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_chat_answers_quality_verdict",
        "chat_answers",
        "quality_verdict IS NULL OR quality_verdict IN "
        "('SUFFICIENT', 'TOO_VAGUE', 'NON_ANSWER', 'REFUSAL', 'WANT_END')",
    )
