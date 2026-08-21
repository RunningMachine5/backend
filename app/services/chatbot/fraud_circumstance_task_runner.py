"""턴 커밋 후 별도 DB 세션에서 사기 정황을 추출하고 채점한다."""

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


# 같은 세션의 전체 재채점이 서로 덮어쓰지 않도록 추출 작업을 직렬화한다.
_session_locks: dict[str, Lock] = defaultdict(Lock)
_session_locks_guard = Lock()


def run_fraud_circumstance_extraction(
    task: FraudCircumstanceExtractionTask,
    *,
    extractor: FraudCircumstanceExtractor | None = None,
) -> None:
    """사기 정황과 점수를 커밋한 뒤 갱신 이벤트를 발행한다."""

    try:
        with _lock_for(task.chat_session_id):
            with Session(engine) as session:
                _extract_and_score(session, task, extractor)
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
        # 백그라운드 추출 실패는 고객 상담에 영향을 주지 않는다.
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
