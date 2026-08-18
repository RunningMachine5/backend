"""채팅 세션 생성과 통합 메일 수신 예정 주소 계산을 검증한다."""

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
            id=1,
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
            customer_id=1,
            source_account_number="source-0001",
            recipient_account_number="recipient-0001",
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

    def test_creates_session_with_top_types_and_notification_target(self) -> None:
        transaction_id = self._seed_transaction()

        result = self._create(
            transaction_id,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )

        chat_session = result.chat_session
        self.assertTrue(result.created)
        self.assertFalse(result.used_fallback_email)
        self.assertEqual(chat_session.chat_session_id, "CHAT-FIRST")
        self.assertEqual(chat_session.status, ChatSessionStatus.URL_SENT.value)
        self.assertEqual(result.notified_email, "hong@example.com")
        self.assertIsNone(chat_session.notified_email)
        self.assertIsNone(chat_session.email_sent_at)
        self.assertEqual(
            chat_session.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

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

    def test_marks_customer_born_60_years_ago_as_older(self) -> None:
        transaction_id = self._seed_transaction(birth_year=NOW.year - 60)

        result = self._create(transaction_id)

        self.assertTrue(result.chat_session.is_older)

    def test_marks_younger_customer_as_not_older(self) -> None:
        transaction_id = self._seed_transaction(birth_year=NOW.year - 59)

        result = self._create(transaction_id)

        self.assertFalse(result.chat_session.is_older)

    def test_second_call_returns_existing_session_without_duplicate_row(
        self,
    ) -> None:
        """rule_replay 재처리로 다시 불려도 세션은 한 행뿐이다."""

        transaction_id = self._seed_transaction()
        first = self._create(transaction_id)

        second = self._create(transaction_id)

        self.assertFalse(second.created)
        self.assertEqual(
            second.chat_session.chat_session_id,
            first.chat_session.chat_session_id,
        )
        session_count = self.session.exec(
            select(func.count()).select_from(ChatSession)
        ).one()
        self.assertEqual(session_count, 1)

    def test_raises_when_transaction_is_missing(self) -> None:
        with self.assertRaises(ChatSessionTargetNotFoundError):
            self._creator().create(transaction_id=999)


if __name__ == "__main__":
    unittest.main()
