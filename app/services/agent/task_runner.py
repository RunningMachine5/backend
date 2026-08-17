"""거래 API 응답 이후 별도 Session에서 Agent Workflow를 실행한다."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.db import engine
from app.dto.agent import AgentInputDTO
from app.repositories.chat_session import ChatSessionRepository
from app.services.agent.workflow_factory import create_agent_workflow
from app.services.chatbot.session_event_broker import chat_session_event_broker

logger = logging.getLogger(__name__)


def run_agent_task(agent_input: AgentInputDTO) -> None:
    """별도 DB Session으로 Agent를 실행하고 신규 챗봇 세션 상태를 발행한다."""

    created_chat_session = None
    try:
        with Session(engine) as session:
            # 기존 챗봇 세션이 있는지 조회한다
            chat_session_repository = ChatSessionRepository(session)
            create_agent_workflow(session).run(agent_input)

            # Agent Workflow의 마지막 commit 이후 SSE에 보낼 세션을 조회한다.
            created_chat_session = (
                chat_session_repository.find_by_transaction(
                    agent_input.transaction_id
                )
            )
    except Exception:
        logger.exception(
            "Agent 백그라운드 실행 실패: transaction_id=%s",
            agent_input.transaction_id,
        )
        return
    # =====
    # 새롭게 세션이 생성됐다면 SSE 알림을 날린다 , 프론트에 URL_SENT 됐다고 알려야하므로
    # =====
    if created_chat_session is None:
        return

    try:
        chat_session_event_broker.publish_status_changed(created_chat_session)
    except Exception:
        logger.exception(
            "챗봇 세션 최초 상태 SSE 발행 실패: transaction_id=%s session=%s",
            agent_input.transaction_id,
            created_chat_session.chat_session_id,
        )


def get_agent_task_runner() -> Callable[[AgentInputDTO], None]:
    """API 테스트에서 실제 Agent 실행 함수를 교체할 수 있게 제공한다."""

    return run_agent_task


AgentTaskRunnerDep = Annotated[
    Callable[[AgentInputDTO], None],
    Depends(get_agent_task_runner),
]


__all__ = [
    "AgentTaskRunnerDep",
    "get_agent_task_runner",
    "run_agent_task",
]
