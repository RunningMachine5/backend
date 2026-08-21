"""거래별 사기 정황 점수를 SSE 구독자에게 발행한다."""

from __future__ import annotations

from sqlmodel import Session

from app.data.model.chatbot import FraudTypeScoreAfterChat
from app.domain.fraud_type_codes import get_fraud_type_display_name
from app.dto.chatbot import ChatFraudTypeScoreResponse
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.chat_score_event_broker import chat_score_event_broker


CHAT_SCORE_UPDATED_EVENT = "chat_score_updated"


def publish_chat_score_update(session: Session, transaction_id: int) -> None:
    """커밋된 최신 사기 정황 점수를 구독자에게 발행한다."""

    scores = ChatSessionRepository(session).get_fraud_type_scores(transaction_id)
    if scores is None:
        return

    chat_score_event_broker.publish(
        transaction_id,
        event=CHAT_SCORE_UPDATED_EVENT,
        data={
            "transaction_id": transaction_id,
            "type_scores": [
                response.model_dump()
                for response in build_type_score_responses(scores)
            ],
        },
    )


def build_type_score_responses(
    scores: FraudTypeScoreAfterChat | None,
) -> list[ChatFraudTypeScoreResponse]:
    """점수 내림차순으로 정렬한다. 동점이면 코드 오름차순이라 순서가 흔들리지 않는다."""

    if scores is None:
        return []

    return [
        ChatFraudTypeScoreResponse(
            type_code=type_code,
            display_name=get_fraud_type_display_name(type_code),
            score=int(score),
        )
        for type_code, score in sorted(
            scores.type_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


__all__ = [
    "CHAT_SCORE_UPDATED_EVENT",
    "build_type_score_responses",
    "publish_chat_score_update",
]
