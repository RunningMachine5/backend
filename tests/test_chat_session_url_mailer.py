"""B.7 챗봇 접속 안내 메일 조립 검증. SMTP 는 Fake 전송기로 대체한다."""

import unittest
from datetime import UTC, datetime
from email.message import EmailMessage

from app.core.config import CHAT_BASE_URL, CHAT_FALLBACK_EMAIL
from app.services.chatbot.messages import CHAT_URL_EMAIL_SUBJECT
from app.services.chatbot.session_url_mailer import (
    ChatSessionUrlMailer,
    build_chat_url,
)


TRANSACTION_DATETIME = datetime(2026, 8, 15, 14, 3, tzinfo=UTC)


class FakeEmailMessageSender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)
        if self.error is not None:
            raise self.error


class ChatSessionUrlMailerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sender = FakeEmailMessageSender()
        self.mailer = ChatSessionUrlMailer(
            self.sender,
            from_email="noreply@fdshield.local",
            from_name="FDShield",
        )

    def _send(self, **overrides) -> None:
        kwargs = {
            "chat_session_id": "CHAT-0001",
            "recipient_email": "hong@example.com",
            "customer_name": "홍길동",
            "transaction_datetime": TRANSACTION_DATETIME,
            "transaction_amount": -1_234_000,
            "used_fallback_email": False,
        }
        kwargs.update(overrides)
        self.mailer.send(**kwargs)

    def test_builds_subject_recipient_and_body(self) -> None:
        self._send()

        message = self.sender.messages[0]
        body = message.get_content()
        self.assertEqual(message["Subject"], CHAT_URL_EMAIL_SUBJECT)
        self.assertEqual(message["To"], "hong@example.com")
        self.assertEqual(message["From"], "FDShield <noreply@fdshield.local>")
        self.assertIn("안녕하세요, 홍길동님.", body)
        self.assertIn("거래 일시: 2026-08-15 14:03", body)
        # 금액은 부호를 떼고, 방향은 부호로 판정한다(B.1과 같은 표기).
        self.assertIn("거래 금액: 1,234,000원 출금", body)
        self.assertIn(f"{CHAT_BASE_URL}/chat/CHAT-0001", body)

    def test_uses_default_name_when_customer_name_is_missing(self) -> None:
        for name in (None, "", "  "):
            with self.subTest(customer_name=name):
                self.setUp()
                self._send(customer_name=name)

                self.assertIn(
                    "안녕하세요, 고객님.",
                    self.sender.messages[0].get_content(),
                )

    def test_marks_deposit_direction_for_positive_amount(self) -> None:
        self._send(transaction_amount=500_000)

        self.assertIn(
            "거래 금액: 500,000원 입금",
            self.sender.messages[0].get_content(),
        )

    def test_logs_fallback_recipient(self) -> None:
        with self.assertLogs(
            "app.services.chatbot.session_url_mailer",
            "INFO",
        ) as captured:
            self._send(
                recipient_email=CHAT_FALLBACK_EMAIL,
                used_fallback_email=True,
            )

        logged = captured.output[0]
        self.assertIn(f"수신자={CHAT_FALLBACK_EMAIL} (기본 주소)", logged)
        self.assertIn(build_chat_url("CHAT-0001"), logged)

    def test_propagates_transport_failure(self) -> None:
        """발송 실패 처리는 호출부(ChatSessionCreator)가 한다."""

        self.sender = FakeEmailMessageSender(error=RuntimeError("smtp"))
        self.mailer = ChatSessionUrlMailer(
            self.sender,
            from_email="noreply@fdshield.local",
        )

        with self.assertRaises(RuntimeError):
            self._send()


if __name__ == "__main__":
    unittest.main()
