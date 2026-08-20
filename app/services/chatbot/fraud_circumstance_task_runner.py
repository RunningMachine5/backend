"""턴 응답 이후 별도 Session에서 사기 정황 추출·채점을 실행한다(PRD 2.6).

추출 결과는 그 턴에 고객에게 보낼 메시지에 쓰이지 않고 담당자 화면(점수)만 바꾸므로,
추출 LLM 호출을 턴 응답 경로에서 빼 백그라운드로 돌린다. 고객은 가이드 응답과 다음
질문을 추출이 끝나기를 기다리지 않고 받는다. 갱신된 점수는 담당자 화면이 SSE
(``chat_score_updated``)로 받는다(PRD 2.7).

Agent 백그라운드 실행([task_runner.py](../agent/task_runner.py))과 같은 방침이다 —
요청 응답 뒤에 도는 작업이라 실패해도 밖으로 올리지 않고 로그만 남긴다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import Lock
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.db import engine
from app.data.model.chatbot import ChatAnswer
from app.dto.chatbot import FraudCircumstanceExtractionTask
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.chat_score_publisher import publish_chat_score_update
from app.services.chatbot.chat_scoring import rescore_chat_session
from app.services.chatbot.extractors import (
    ChatbotExtractionError,
    FraudCircumstanceExtractor,
)

logger = logging.getLogger(__name__)


# 세션 하나의 추출 작업은 한 번에 하나만 돈다. 채점이 "정황 전체를 다시 읽어
# 덮어쓰는" 방식이라, 같은 세션의 두 턴이 동시에 끝나면 늦게 시작한 쪽의 갱신을
# 먼저 시작한 쪽이 옛 값으로 덮을 수 있기 때문이다. 락은 세션 수만큼만 쌓이고
# 진행 상태 자체가 이미 프로세스에 묶여 있으므로(InMemorySaver, 스키마 3.4)
# 프로세스 안에서만 유효한 직렬화로 충분하다.
_session_locks: dict[str, Lock] = defaultdict(Lock)
_session_locks_guard = Lock()


def run_fraud_circumstance_extraction(
    task: FraudCircumstanceExtractionTask,
    *,
    extractor: FraudCircumstanceExtractor | None = None,
) -> None:
    """채택된 답변 하나에서 사기 정황을 추출해 저장하고 점수를 갱신한다.

    커밋까지 마친 뒤에 SSE를 발행한다. 담당자 화면이 아직 커밋되지 않은 점수를
    먼저 받는 일은 없다.
    """

    try:
        with _lock_for(task.chat_session_id):
            with Session(engine) as session:
                _extract_and_score(session, task, extractor)
            # 발행은 커밋 뒤에 별도 세션으로 읽어 보낸다.
            with Session(engine) as session:
                publish_chat_score_update(session, task.transaction_id)
    except Exception:
        logger.exception(
            "사기 정황 추출 백그라운드 실행 실패: session=%s answer_id=%s",
            task.chat_session_id,
            task.answer_id,
        )


def _extract_and_score(
    session: Session,
    task: FraudCircumstanceExtractionTask,
    extractor: FraudCircumstanceExtractor | None,
) -> None:
    """추출·저장·채점을 한 트랜잭션으로 처리한다."""

    repository = ChatSessionRepository(session)
    chat_session = repository.get(task.chat_session_id)
    if chat_session is None:
        logger.warning(
            "사기 정황을 저장할 채팅 세션이 없습니다: session=%s",
            task.chat_session_id,
        )
        return

    try:
        extraction = (extractor or FraudCircumstanceExtractor()).extract(
            user_answers=task.message_text
        )
    except ChatbotExtractionError:
        # 추출 실패는 상담을 막지 않는다. 이 답변의 정황만 비고 다음 턴에서 다시 추출한다.
        logger.warning(
            "사기 정황 추출을 건너뜁니다: session=%s",
            task.chat_session_id,
        )
        return

    source_answer = session.get(ChatAnswer, task.answer_id)
    for circumstance in extraction.fraud_circumstances:
        repository.add_fraud_circumstance(
            chat_session,
            circumstance_code=circumstance.type,
            evidence=circumstance.evidence,
            source_answer=source_answer,
        )

    # 이 턴에서 새 정황이 없었어도(전부 중복이거나 0건) 재계산 자체는 저렴하므로 그대로 갱신한다.
    rescore_chat_session(repository, chat_session)
    session.commit()


def _lock_for(chat_session_id: str) -> Lock:
    with _session_locks_guard:
        return _session_locks[chat_session_id]


def get_fraud_circumstance_task_runner() -> (
    Callable[[FraudCircumstanceExtractionTask], None]
):
    """API 테스트에서 실제 추출 실행 함수를 교체할 수 있게 제공한다."""

    return run_fraud_circumstance_extraction


FraudCircumstanceTaskRunnerDep = Annotated[
    Callable[[FraudCircumstanceExtractionTask], None],
    Depends(get_fraud_circumstance_task_runner),
]


__all__ = [
    "FraudCircumstanceTaskRunnerDep",
    "get_fraud_circumstance_task_runner",
    "run_fraud_circumstance_extraction",
]
