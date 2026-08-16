import unittest
from datetime import UTC, date, datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.domain.agent_status import ClassificationStatus
from app.dto.agent import FraudAlertEmailCommand
from app.services.chatbot.session_alert_notifier import ChatSessionAlertNotifier
from app.services.chatbot.session_creator import ChatSessionCreationResult
from app.services.chatbot.session_creator import ChatSessionCreator


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
        )

    @staticmethod
    def _command() -> FraudAlertEmailCommand:
        return FraudAlertEmailCommand(
            transaction_id=1,
            primary_suspected_type="ACCOUNT_TAKEOVER",
            secondary_suspected_type="MESSENGER_PHISHING",
            classification_status=ClassificationStatus.CONFIDENT,
        )


class ChatSessionAlertNotifierIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Customer.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        ChatSession.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.transaction_id = self._seed_transaction()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_reuses_one_session_and_sends_integrated_email_once(self) -> None:
        email_notifier = FakeEmailNotifier()
        notifier = self._notifier(email_notifier)

        notifier.send(self._command())
        self.session.commit()
        notifier.send(self._command())
        self.session.commit()

        chat_session = self.session.exec(select(ChatSession)).one()
        self.assertEqual(
            self.session.exec(select(func.count()).select_from(ChatSession)).one(),
            1,
        )
        self.assertEqual(len(email_notifier.calls), 1)
        self.assertEqual(chat_session.status, ChatSessionStatus.URL_SENT.value)
        self.assertEqual(chat_session.notified_email, "hong@example.com")
        self.assertEqual(
            chat_session.top_fraud_types,
            ["ACCOUNT_TAKEOVER", "MESSENGER_PHISHING"],
        )

    def test_email_failure_keeps_transaction_and_marks_session_failed(
        self,
    ) -> None:
        notifier = self._notifier(
            FakeEmailNotifier(error=RuntimeError("smtp"))
        )

        with self.assertRaises(RuntimeError):
            notifier.send(self._command())
        self.session.commit()

        self.assertIsNotNone(
            self.session.get(Transaction, self.transaction_id)
        )
        chat_session = self.session.exec(select(ChatSession)).one()
        self.assertEqual(chat_session.status, ChatSessionStatus.FAILED.value)
        self.assertEqual(chat_session.notified_email, "hong@example.com")
        self.assertIsNone(chat_session.email_sent_at)

    def _notifier(self, email_notifier: FakeEmailNotifier):
        creator = ChatSessionCreator(
            self.session,
            chat_session_id_factory=lambda: "CHAT-INTEGRATION",
            now_factory=lambda: NOW,
        )
        return ChatSessionAlertNotifier(
            session=self.session,
            session_creator=creator,
            email_notifier=email_notifier,  # type: ignore[arg-type]
            now_factory=lambda: NOW,
        )

    def _command(self) -> FraudAlertEmailCommand:
        return FraudAlertEmailCommand(
            transaction_id=self.transaction_id,
            primary_suspected_type="ACCOUNT_TAKEOVER",
            secondary_suspected_type="MESSENGER_PHISHING",
            classification_status=ClassificationStatus.CONFIDENT,
        )

    def _seed_transaction(self) -> int:
        customer = Customer(
            id="CUST-INTEGRATION",
            name="홍길동",
            birth_date=date(1990, 3, 1),
            gender="male",
            identification_number="900301-1234567",
            email="hong@example.com",
            registration_datetime=NOW,
            credit_rating=3,
            loan_type="a",
        )
        transaction = Transaction(
            customer_id=customer.id,
            source_account_number="source-integration",
            transaction_datetime=NOW,
            transaction_amount=-1_000_000,
            channel="mobile",
            type_general_automatic="general",
            access_medium="a",
            num_connection_failure=0,
            location="서울특별시 중구",
            rooting_jailbreak_indicator=False,
            mobile_roaming_indicator=False,
            vpn_indicator=False,
            flag_terminal_malicious_behavior_1=False,
            flag_terminal_malicious_behavior_2=False,
            flag_terminal_malicious_behavior_3=False,
            flag_terminal_malicious_behavior_5=False,
            flag_terminal_malicious_behavior_6=False,
        )
        self.session.add(customer)
        self.session.add(transaction)
        self.session.commit()
        assert transaction.id is not None
        return transaction.id


if __name__ == "__main__":
    unittest.main()
