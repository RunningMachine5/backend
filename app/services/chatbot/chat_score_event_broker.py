"""거래별 사기 정황 점수 SSE 발행/구독을 관리한다.

대시보드 브로커(``dashboard_event_broker``)는 전체 구독자에게 한 이벤트를 그대로
뿌리는 단일 브로드캐스트다. 챗봇 점수는 동시에 여러 상담이 진행되는데 담당자
화면은 자신이 연 거래의 점수만 받아야 하므로, 구독을 거래(``transaction_id``)
단위로 나눈다.
"""

from collections import defaultdict
from dataclasses import dataclass
from queue import Queue
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class ChatScoreEvent:
    event: str
    data: dict[str, Any]


class ChatScoreEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[Queue[ChatScoreEvent]]] = defaultdict(set)
        self._lock = Lock()

    def subscribe(self, transaction_id: int) -> Queue[ChatScoreEvent]:
        subscriber_queue: Queue[ChatScoreEvent] = Queue()

        with self._lock:
            self._subscribers[transaction_id].add(subscriber_queue)

        return subscriber_queue

    def unsubscribe(
        self,
        transaction_id: int,
        subscriber_queue: Queue[ChatScoreEvent],
    ) -> None:
        with self._lock:
            queues = self._subscribers.get(transaction_id)
            if queues is None:
                return
            queues.discard(subscriber_queue)
            if not queues:
                del self._subscribers[transaction_id]

    def publish(
        self,
        transaction_id: int,
        *,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        chat_score_event = ChatScoreEvent(event=event, data=data or {})

        with self._lock:
            subscriber_queues = list(self._subscribers.get(transaction_id, ()))

        for subscriber_queue in subscriber_queues:
            subscriber_queue.put(chat_score_event)


chat_score_event_broker = ChatScoreEventBroker()
