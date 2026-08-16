"""세션 상태 변경 in-process pub/sub 검증 (PRD 2.7)."""

import unittest
from queue import Empty

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.services.chatbot.session_event_broker import (
    CHAT_SESSION_STATUS_CHANGED_EVENT,
    ChatSessionEventBroker,
)


def _chat_session(
    *,
    status: ChatSessionStatus = ChatSessionStatus.HANDOFF_REQUESTED,
) -> ChatSession:
    return ChatSession(
        chat_session_id="CHAT-0001",
        transaction_id=77,
        status=status.value,
    )


class ChatSessionEventBrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = ChatSessionEventBroker()

    def test_publishes_transaction_id_session_id_and_status(self) -> None:
        subscriber = self.broker.subscribe()

        self.broker.publish_status_changed(_chat_session())

        event = subscriber.get_nowait()
        self.assertEqual(event.event, CHAT_SESSION_STATUS_CHANGED_EVENT)
        self.assertEqual(event.payload.transaction_id, 77)
        self.assertEqual(event.payload.chat_session_id, "CHAT-0001")
        self.assertEqual(event.payload.status, "HANDOFF_REQUESTED")

    def test_every_subscriber_receives_a_copy(self) -> None:
        """대시보드 여러 개가 붙어도 각 연결이 같은 이벤트를 받는다."""

        first = self.broker.subscribe()
        second = self.broker.subscribe()

        self.broker.publish_status_changed(_chat_session())

        self.assertEqual(first.get_nowait().payload.status, "HANDOFF_REQUESTED")
        self.assertEqual(second.get_nowait().payload.status, "HANDOFF_REQUESTED")

    def test_unsubscribed_queue_stops_receiving(self) -> None:
        subscriber = self.broker.subscribe()
        self.broker.unsubscribe(subscriber)

        self.broker.publish_status_changed(_chat_session())

        with self.assertRaises(Empty):
            subscriber.get_nowait()

    def test_publishing_without_subscribers_is_a_no_op(self) -> None:
        self.broker.publish_status_changed(_chat_session())

    def test_rejects_status_outside_contract(self) -> None:
        """페이로드는 ChatSessionStatus 5종만 싣는다."""

        chat_session = _chat_session()
        chat_session.status = "UNKNOWN"
        self.broker.subscribe()

        with self.assertRaises(ValueError):
            self.broker.publish_status_changed(chat_session)


if __name__ == "__main__":
    unittest.main()
