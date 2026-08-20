"""거래별 사기 정황 점수를 SSE 구독자에게 발행한다(PRD 2.7).

발행 지점이 둘이라 라우터에서 꺼내 서비스로 옮겼다.

- 답변 턴이 커밋된 뒤 라우터([chat.py](../../api/chat.py))가 그때의 값을 발행한다.
- 사기 정황 추출이 백그라운드에서 끝난 뒤
  [fraud_circumstance_task_runner.py](fraud_circumstance_task_runner.py)가
  갱신된 값을 발행한다. **점수가 실제로 바뀌는 것은 이쪽이다.**
"""

from __future__ import annotations

from sqlmodel import Session

from app.data.model.chatbot import FraudTypeScoreAfterChat
from app.domain.fraud_type_codes import get_fraud_type_display_name
from app.dto.chatbot import ChatFraudTypeScoreResponse
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.chat_score_event_broker import chat_score_event_broker


CHAT_SCORE_UPDATED_EVENT = "chat_score_updated"


def publish_chat_score_update(session: Session, transaction_id: int) -> None:
    """커밋된 최신 사기 정황 점수를 구독자에게 발행한다.

    구독자가 없는 거래는 브로커가 즉시 버리므로 SSE를 아무도 안 듣는 상담이
    다수여도 비용이 없다. 값이 그대로인 발행도 구독자가 같은 값을 다시 받을
    뿐이라 무해하다.
    """

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
