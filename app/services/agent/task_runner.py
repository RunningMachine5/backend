"""거래 API 응답 이후 별도 Session에서 Agent Workflow를 실행한다."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.db import engine
from app.dto.agent import AgentInputDTO
from app.services.agent.workflow_factory import create_agent_workflow

logger = logging.getLogger(__name__)


def run_agent_task(agent_input: AgentInputDTO) -> None:
    """요청 Session과 분리된 DB Session으로 Agent를 실행한다."""

    try:
        with Session(engine) as session:
            create_agent_workflow(session).run(agent_input)
    except Exception:
        logger.exception(
            "Agent 백그라운드 실행 실패: transaction_id=%s",
            agent_input.transaction_id,
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
