# SSE 연결 관리, dashboard_updated 이벤트 전송

from dataclasses import dataclass
from queue import Queue
from threading import Lock

@dataclass(frozen=True)
class DashboardEvent:
    event: str
    data: dict[str, Any]

class DashboardEventBroker:
    def __init__(self) -> None:
        self._subscribers: set[Queue[DashboardEvent]] = set()
        self._lock = Lock()

    def subscribe(self) -> Queue[DashboardEvent]:
        subscriber_queue: Queue[DashboardEvent] = Queue()

        with self._lock:
            self._subscribers.add(subscriber_queue)

        return subscriber_queue

    def unsubscribe(self, subscriber_queue: Queue[DashboardEvent]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber_queue)

    def publish(
            self,
            event: str,
            data: dict[str, Any] | None = None,
    ) -> None:
        dashboard_event = DashboardEvent(
            event=event,
            data=data or {},
        )

        with self._lock:
            subscriber_queues = list(self._subscribers)

        for subscriber_queue in subscriber_queues:
            subscriber_queue.put(dashboard_event)

dashboard_event_broker = DashboardEventBroker()