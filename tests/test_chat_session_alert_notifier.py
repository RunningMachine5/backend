import unittest
from datetime import UTC, datetime

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.domain.agent_status import ClassificationStatus
from app.dto.agent import FraudAlertEmailCommand
from app.services.chatbot.session_alert_notifier import ChatSessionAlertNotifier
from app.services.chatbot.session_creator import ChatSessionCreationResult


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeSessionCreator:
    def __init__(self, result: ChatSessionCreationResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs) -> ChatSessionCreationResult:
        self.calls.append(kwargs)
        return self.result


class FakeEmailNotifier:
    def __init__(
        self,
        *,
        sent: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.sent = sent
        self.error = error
        self.calls: list[tuple[FraudAlertEmailCommand, str | None]] = []

    def send(self, command, *, chatbot_url=None) -> bool:
        self.calls.append((command, chatbot_url))
        if self.error is not None:
            raise self.error
        return self.sent


class FakeChatSessionRepository:
    def __init__(self) -> None:
        self.url_sent_calls: list[tuple[ChatSession, str, datetime]] = []
        self.status_calls: list[tuple[ChatSession, ChatSessionStatus]] = []

    def record_url_sent(
        self,
        chat_session,
        *,
        notified_email,
        email_sent_at,
    ) -> None:
        self.url_sent_calls.append(
            (chat_session, notified_email, email_sent_at)
        )
        chat_session.status = ChatSessionStatus.URL_SENT.value
        chat_session.notified_email = notified_email
        chat_session.email_sent_at = email_sent_at

    def update_status(self, chat_session, status) -> None:
        self.status_calls.append((chat_session, status))
        chat_session.status = status.value


class ChatSessionAlertNotifierTest(unittest.TestCase):
    def test_creates_session_and_sends_one_agent_email_with_session_url(self) -> None:
        creator = FakeSessionCreator(self._creation(created=True))
        email_notifier = FakeEmailNotifier()
        repository = FakeChatSessionRepository()
        notifier = self._notifier(creator, email_notifier, repository)

        notifier.send(self._command())

        self.assertEqual(
            creator.calls,
            [
                {
                    "transaction_id": 1,
                    "top_fraud_types": [
                        "ACCOUNT_TAKEOVER",
                        "MESSENGER_PHISHING",
                    ],
                    "send_notification": False,
                }
            ],
        )
        self.assertEqual(len(email_notifier.calls), 1)
        self.assertTrue(
            email_notifier.calls[0][1].endswith("/chat/CHAT-001")
        )
        self.assertEqual(len(repository.url_sent_calls), 1)
        self.assertEqual(repository.url_sent_calls[0][1], "customer@example.com")
        self.assertEqual(repository.url_sent_calls[0][2], NOW)

    def test_existing_session_does_not_resend_email(self) -> None:
        creator = FakeSessionCreator(self._creation(created=False))
        email_notifier = FakeEmailNotifier()
        repository = FakeChatSessionRepository()

        self._notifier(creator, email_notifier, repository).send(self._command())

        self.assertEqual(email_notifier.calls, [])
        self.assertEqual(repository.url_sent_calls, [])

    def test_marks_session_failed_when_email_sender_raises(self) -> None:
        creator = FakeSessionCreator(self._creation(created=True))
        email_notifier = FakeEmailNotifier(error=RuntimeError("smtp"))
        repository = FakeChatSessionRepository()

        with self.assertRaises(RuntimeError):
            self._notifier(creator, email_notifier, repository).send(
                self._command()
            )

        self.assertEqual(len(repository.status_calls), 1)
        self.assertEqual(
            repository.status_calls[0][1],
            ChatSessionStatus.FAILED,
        )
        self.assertEqual(
            repository.status_calls[0][0].notified_email,
            "customer@example.com",
        )

    def test_marks_session_failed_when_email_context_is_missing(self) -> None:
        creator = FakeSessionCreator(self._creation(created=True))
        email_notifier = FakeEmailNotifier(sent=False)
        repository = FakeChatSessionRepository()

        with self.assertLogs(
            "app.services.chatbot.session_alert_notifier",
            "WARNING",
        ):
            self._notifier(creator, email_notifier, repository).send(
                self._command()
            )

        self.assertEqual(len(repository.status_calls), 1)
        self.assertEqual(
            repository.status_calls[0][1],
            ChatSessionStatus.FAILED,
        )

    @staticmethod
    def _notifier(creator, email_notifier, repository):
        return ChatSessionAlertNotifier(
            session=None,  # type: ignore[arg-type]
            session_creator=creator,  # type: ignore[arg-type]
            email_notifier=email_notifier,
            repository=repository,  # type: ignore[arg-type]
            now_factory=lambda: NOW,
        )

    @staticmethod
    def _creation(*, created: bool) -> ChatSessionCreationResult:
        return ChatSessionCreationResult(
            chat_session=ChatSession(
                chat_session_id="CHAT-001",
                transaction_id=1,
            ),
            created=created,
            notified_email="customer@example.com",
            used_fallback_email=False,
            email_sent=not created,
        )

    @staticmethod
    def _command() -> FraudAlertEmailCommand:
        return FraudAlertEmailCommand(
            transaction_id=1,
            primary_suspected_type="ACCOUNT_TAKEOVER",
            secondary_suspected_type="MESSENGER_PHISHING",
            classification_status=ClassificationStatus.CONFIDENT,
        )


if __name__ == "__main__":
    unittest.main()
