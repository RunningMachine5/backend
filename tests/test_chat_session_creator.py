"""채팅 세션 생성과 URL 안내 발송(PRD 2.1) 분기 검증.

SMTP 는 실제로 부르지 않는다. 발송기는 프로토콜을 만족하는 Fake 로 주입한다.
"""

import unittest
from datetime import UTC, date, datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.core.config import CHAT_FALLBACK_EMAIL
from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.services.chatbot.session_creator import (
    ChatSessionCreator,
    ChatSessionTargetNotFoundError,
)


NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


class FakeChatSessionUrlNotifier:
    """발송 인자를 기록하고, 필요하면 SMTP 장애를 흉내낸다."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def send(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class ChatSessionCreatorTest(unittest.TestCase):
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
        self.notifier = FakeChatSessionUrlNotifier()
        self.ids = iter(["CHAT-FIRST", "CHAT-SECOND"])

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    # ------------------------------------------------------------------
    # 픽스처
    # ------------------------------------------------------------------

    def _creator(self) -> ChatSessionCreator:
        return ChatSessionCreator(
            self.session,
            notifier=self.notifier,
            chat_session_id_factory=lambda: next(self.ids),
            now_factory=lambda: NOW,
        )

    def _seed_transaction(
        self,
        *,
        email: str | None = "hong@example.com",
        birth_year: int = 1990,
    ) -> int:
        customer = Customer(
            id="CUST-1",
            name="홍길동",
            birth_date=date(birth_year, 3, 1),
            gender="male",
            identification_number="900301-1234567",
            email=email,
            registration_datetime=NOW,
            credit_rating=3,
            loan_type="a",
        )
        transaction = Transaction(
            customer_id="CUST-1",
            source_account_number="source-0001",
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

    def _create(self, transaction_id: int, **kwargs):
        result = self._creator().create(transaction_id=transaction_id, **kwargs)
        self.session.commit()
        return result

    # ------------------------------------------------------------------
    # 테스트
    # ------------------------------------------------------------------

    def test_creates_session_and_mails_customer_email(self) -> None:
        transaction_id = self._seed_transaction()

        result = self._create(
            transaction_id,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )

        chat_session = result.chat_session
        self.assertTrue(result.created)
        self.assertTrue(result.email_sent)
        self.assertFalse(result.used_fallback_email)
        self.assertEqual(chat_session.chat_session_id, "CHAT-FIRST")
        self.assertEqual(chat_session.status, ChatSessionStatus.URL_SENT.value)
        self.assertEqual(chat_session.notified_email, "hong@example.com")
        # SQLite 는 tz 정보를 보존하지 않으므로 시각만 비교한다.
        self.assertIsNotNone(chat_session.email_sent_at)
        self.assertEqual(chat_session.email_sent_at.replace(tzinfo=UTC), NOW)
        self.assertEqual(
            chat_session.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

        self.assertEqual(len(self.notifier.calls), 1)
        call = self.notifier.calls[0]
        self.assertEqual(call["chat_session_id"], "CHAT-FIRST")
        self.assertEqual(call["recipient_email"], "hong@example.com")
        self.assertEqual(call["customer_name"], "홍길동")
        self.assertEqual(call["transaction_amount"], -1_000_000)
        self.assertFalse(call["used_fallback_email"])

    def test_falls_back_to_default_email_when_customer_email_is_blank(
        self,
    ) -> None:
        """NULL·빈 문자열·공백뿐인 주소는 모두 기본 주소로 대체한다(PRD 2.1)."""

        for blank in (None, "", "   "):
            with self.subTest(email=blank):
                self.setUp()
                transaction_id = self._seed_transaction(email=blank)

                result = self._create(transaction_id)

                self.assertTrue(result.used_fallback_email)
                self.assertEqual(result.notified_email, CHAT_FALLBACK_EMAIL)
                self.assertEqual(
                    result.chat_session.status,
                    ChatSessionStatus.URL_SENT.value,
                )
                call = self.notifier.calls[0]
                self.assertEqual(call["recipient_email"], CHAT_FALLBACK_EMAIL)
                self.assertTrue(call["used_fallback_email"])

    def test_marks_session_failed_when_sending_raises(self) -> None:
        """발송 실패는 예외를 올리지 않고 FAILED 로 남긴다(스키마 3.3)."""

        self.notifier = FakeChatSessionUrlNotifier(error=RuntimeError("smtp"))
        transaction_id = self._seed_transaction()

        with self.assertLogs("app.services.chatbot.session_creator", "ERROR"):
            result = self._create(transaction_id)

        chat_session = result.chat_session
        self.assertTrue(result.created)
        self.assertFalse(result.email_sent)
        self.assertEqual(chat_session.status, ChatSessionStatus.FAILED.value)
        self.assertIsNone(chat_session.email_sent_at)
        # 어느 주소로 시도했는지는 남긴다.
        self.assertEqual(chat_session.notified_email, "hong@example.com")

    def test_marks_customer_born_60_years_ago_as_older(self) -> None:
        transaction_id = self._seed_transaction(birth_year=NOW.year - 60)

        result = self._create(transaction_id)

        self.assertTrue(result.chat_session.is_older)

    def test_marks_younger_customer_as_not_older(self) -> None:
        transaction_id = self._seed_transaction(birth_year=NOW.year - 59)

        result = self._create(transaction_id)

        self.assertFalse(result.chat_session.is_older)

    def test_second_call_returns_existing_session_without_resending(
        self,
    ) -> None:
        """rule_replay 재처리로 다시 불려도 세션과 안내는 한 번뿐이다."""

        transaction_id = self._seed_transaction()
        first = self._create(transaction_id)

        second = self._create(transaction_id)

        self.assertFalse(second.created)
        self.assertTrue(second.email_sent)
        self.assertEqual(
            second.chat_session.chat_session_id,
            first.chat_session.chat_session_id,
        )
        self.assertEqual(len(self.notifier.calls), 1)
        session_count = self.session.exec(
            select(func.count()).select_from(ChatSession)
        ).one()
        self.assertEqual(session_count, 1)

    def test_raises_when_transaction_is_missing(self) -> None:
        with self.assertRaises(ChatSessionTargetNotFoundError):
            self._creator().create(transaction_id=999)


if __name__ == "__main__":
    unittest.main()
