"""채팅 세션 상태 변경을 담당자 대시보드로 흘려보내는 in-process pub/sub.

설계는 docs/customer-chatbot/README.md 2.7 이다. 대시보드는 SSE 연결 하나로 모든
세션의 상태 변경을 받고 ``transaction_id`` 로 목록 항목을 갱신한다. 전체 세션 스냅샷은
보내지 않으며, 최초 접속·재연결 시 거래별 상태 조회로 현재값을 복구한다.

**다중 인스턴스는 고려하지 않는다**(MVP 전제). 이벤트는 이 프로세스의 구독자에게만 간다.

발행 시점 규칙: **커밋을 소유한 쪽이 커밋 직후에 발행한다.** 롤백될 수 있는 상태를
대시보드에 먼저 보여주지 않기 위해서다. 턴 단위 커밋을 소유한
[customer_chatbot_pipeline.py](../../pipelines/customer_chatbot_pipeline.py)는 스스로 발행하고,
Agent가 처음 만든 세션은 사건 저장 커밋이 끝난 뒤
[task_runner.py](../agent/task_runner.py)가 발행한다.

전송 계층(SSE 포맷·keep-alive)은 [app/api/chat.py](../../api/chat.py)에 있고
여기서는 큐 팬아웃만 한다. 대시보드 전역 이벤트를 다루는
[dashboard_event_broker.py](../dashboard/dashboard_event_broker.py)와 같은 구조이며,
채팅 세션 이벤트는 페이로드 계약이 다르고 구독자도 다르므로 큐를 분리한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from threading import Lock

from app.data.model.chatbot import ChatSession
from app.dto.chatbot import ChatSessionStatusChangedEventPayload


# SSE `event:` 이름. 프론트가 이 이름으로 리스너를 건다.
CHAT_SESSION_STATUS_CHANGED_EVENT = "chat_session_status_changed"


@dataclass(frozen=True, slots=True)
class ChatSessionEvent:
    """구독자 큐에 실리는 이벤트 하나."""

    event: str
    payload: ChatSessionStatusChangedEventPayload


class ChatSessionEventBroker:
    """구독자 큐를 관리하고 상태 변경을 모든 구독자에게 복사한다."""

    def __init__(self) -> None:
        self._subscribers: set[Queue[ChatSessionEvent]] = set()
        self._lock = Lock()

    def subscribe(self) -> Queue[ChatSessionEvent]:
        subscriber_queue: Queue[ChatSessionEvent] = Queue()

        with self._lock:
            self._subscribers.add(subscriber_queue)

        return subscriber_queue

    def unsubscribe(self, subscriber_queue: Queue[ChatSessionEvent]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber_queue)

    def publish_status_changed(self, chat_session: ChatSession) -> None:
        """세션의 현재 상태를 그대로 실어 보낸다.

        커밋이 끝난 세션에 대해서만 부른다. 구독자가 없으면 아무 일도 하지 않는다.
        """

        event = ChatSessionEvent(
            event=CHAT_SESSION_STATUS_CHANGED_EVENT,
            payload=ChatSessionStatusChangedEventPayload(
                transaction_id=chat_session.transaction_id,
                chat_session_id=chat_session.chat_session_id,
                status=chat_session.status,
            ),
        )

        with self._lock:
            subscriber_queues = list(self._subscribers)

        for subscriber_queue in subscriber_queues:
            subscriber_queue.put(event)


# 프로세스 전역 브로커. 다중 인스턴스에서는 공유되지 않는다(PRD 2.7).
chat_session_event_broker = ChatSessionEventBroker()


__all__ = [
    "CHAT_SESSION_STATUS_CHANGED_EVENT",
    "ChatSessionEvent",
    "ChatSessionEventBroker",
    "chat_session_event_broker",
]
