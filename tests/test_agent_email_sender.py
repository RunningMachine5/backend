import unittest
from datetime import UTC, datetime
from email.message import EmailMessage

from app.domain.agent_status import ClassificationStatus
from app.dto.agent import FraudAlertEmailCommand
from app.repositories.agent_email import FraudAlertEmailContext
from app.services.agent.email_sender import (
    FraudAlertEmailService,
    build_fraud_alert_email_message,
)


class FakeEmailRepository:
    def __init__(self, context: FraudAlertEmailContext | None) -> None:
        self.context = context

    def get_email_context(self, transaction_id: int):
        del transaction_id
        return self.context


class FakeMessageSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


class FraudAlertEmailServiceTest(unittest.TestCase):
    def test_builds_message_with_transaction_and_two_suspected_types(self) -> None:
        message = build_fraud_alert_email_message(
            self._command(),
            self._context(),
            from_email="alert@fdshield.test",
            from_name="FDShield",
            chatbot_url="https://fdshield.test/customer-chat",
        )

        body = message.get_content()
        self.assertEqual(message["To"], "customer@example.com")
        self.assertIn("9,450,000원", body)
        self.assertIn("mobile", body)
        self.assertIn("계정탈취", body)
        self.assertIn("메신저피싱", body)
        self.assertIn("https://fdshield.test/customer-chat", body)

    def test_sends_message_when_customer_email_exists(self) -> None:
        sender = FakeMessageSender()
        service = FraudAlertEmailService(
            FakeEmailRepository(self._context()),  # type: ignore[arg-type]
            sender,
            from_email="alert@fdshield.test",
        )

        service.send(
            self._command(),
            chatbot_url="https://fdshield.test/chat/CHAT-001",
        )

        self.assertEqual(len(sender.messages), 1)

    def test_uses_required_session_chatbot_url(self) -> None:
        sender = FakeMessageSender()
        service = FraudAlertEmailService(
            FakeEmailRepository(self._context()),  # type: ignore[arg-type]
            sender,
            from_email="alert@fdshield.test",
        )

        sent = service.send(
            self._command(),
            chatbot_url="https://fdshield.test/chat/CHAT-001",
        )

        self.assertTrue(sent)
        self.assertIn(
            "https://fdshield.test/chat/CHAT-001",
            sender.messages[0].get_content(),
        )

    def test_skips_sending_when_email_context_is_missing(self) -> None:
        sender = FakeMessageSender()
        service = FraudAlertEmailService(
            FakeEmailRepository(None),  # type: ignore[arg-type]
            sender,
            from_email="alert@fdshield.test",
        )

        service.send(
            self._command(),
            chatbot_url="https://fdshield.test/chat/CHAT-001",
        )

        self.assertEqual(sender.messages, [])

    @staticmethod
    def _command() -> FraudAlertEmailCommand:
        return FraudAlertEmailCommand(
            transaction_id=1,
            primary_suspected_type="ACCOUNT_TAKEOVER",
            secondary_suspected_type="MESSENGER_PHISHING",
            classification_status=ClassificationStatus.AMBIGUOUS,
        )

    @staticmethod
    def _context() -> FraudAlertEmailContext:
        return FraudAlertEmailContext(
            recipient_email="customer@example.com",
            customer_name="홍길동",
            transaction_datetime=datetime(2026, 8, 13, 10, 30, tzinfo=UTC),
            transaction_amount=9_450_000,
            channel="mobile",
        )


if __name__ == "__main__":
    unittest.main()
