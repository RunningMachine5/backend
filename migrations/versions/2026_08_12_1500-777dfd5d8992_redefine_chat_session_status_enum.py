"""redefine chat session status enum

agent_chat_sessions.status 값을 챗봇 상담 흐름에 맞춰 재정의한다.

* WAITING -> URL_SENT (챗봇URL전송)
* IN_PROGRESS -> IN_PROGRESS (챗봇 상담 진행중, 변경 없음)
* HANDED_OFF -> HANDOFF_REQUESTED (상담사 연결 요청)
* DONE -> DONE (챗봇 상담 완료, 변경 없음)
* CLOSED -> FAILED (챗봇 상담 실패)

Revision ID: 777dfd5d8992
Revises: 3dad8d92c981
Create Date: 2026-08-12 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "777dfd5d8992"
down_revision: Union[str, Sequence[str], None] = "3dad8d92c981"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_TO_NEW = {
    "WAITING": "URL_SENT",
    "HANDED_OFF": "HANDOFF_REQUESTED",
    "CLOSED": "FAILED",
}
_NEW_TO_OLD = {new: old for old, new in _OLD_TO_NEW.items()}


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_chat_sessions_status", "agent_chat_sessions", type_="check"
    )
    # HANDOFF_REQUESTED(18자)가 기존 varchar(16)을 넘어서 컬럼을 넓힌다.
    op.alter_column(
        "agent_chat_sessions",
        "status",
        type_=sa.String(length=32),
        existing_type=sa.String(length=16),
    )
    for old, new in _OLD_TO_NEW.items():
        op.execute(
            sa.text(
                "UPDATE agent_chat_sessions SET status = :new WHERE status = :old"
            ).bindparams(new=new, old=old)
        )
    op.create_check_constraint(
        "ck_agent_chat_sessions_status",
        "agent_chat_sessions",
        "status IN ('URL_SENT', 'IN_PROGRESS', 'HANDOFF_REQUESTED', 'DONE', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_chat_sessions_status", "agent_chat_sessions", type_="check"
    )
    for new, old in _NEW_TO_OLD.items():
        op.execute(
            sa.text(
                "UPDATE agent_chat_sessions SET status = :old WHERE status = :new"
            ).bindparams(old=old, new=new)
        )
    op.alter_column(
        "agent_chat_sessions",
        "status",
        type_=sa.String(length=16),
        existing_type=sa.String(length=32),
    )
    op.create_check_constraint(
        "ck_agent_chat_sessions_status",
        "agent_chat_sessions",
        "status IN ('WAITING', 'IN_PROGRESS', 'HANDED_OFF', 'DONE', 'CLOSED')",
    )
