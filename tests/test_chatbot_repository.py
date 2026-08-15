import unittest
from datetime import UTC, datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.repositories.chat_session import ChatSessionRepository


class ChatSessionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ChatSession.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.repository = ChatSessionRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_create_or_get_is_idempotent_by_transaction_id(self) -> None:
        first = self.repository.create_or_get(
            chat_session_id="CHAT-FIRST",
            transaction_id=101,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )
        second = self.repository.create_or_get(
            chat_session_id="CHAT-SECOND",
            transaction_id=101,
            top_fraud_types=[ACCOUNT_TAKEOVER, FRAUD_USED_ACCOUNT],
        )
        self.session.flush()

        self.assertIs(second, first)
        self.assertEqual(second.chat_session_id, "CHAT-FIRST")
        self.assertEqual(
            second.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )
        session_count = self.session.exec(
            select(func.count()).select_from(ChatSession)
        ).one()
        self.assertEqual(session_count, 1)

    def test_create_or_get_preserves_missing_top_fraud_types(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-FALLBACK",
            transaction_id=102,
        )
        self.session.flush()

        self.assertIsNone(chat_session.top_fraud_types)

    def test_updates_status_and_question_step(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-PROGRESS",
            transaction_id=103,
        )

        self.repository.update_status(
            chat_session,
            ChatSessionStatus.IN_PROGRESS,
        )
        self.repository.update_question_step(chat_session, 2)
        self.session.flush()

        self.assertEqual(chat_session.status, ChatSessionStatus.IN_PROGRESS.value)
        self.assertEqual(chat_session.question_step, 2)

    def test_rejects_negative_question_step(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-INVALID-STEP",
            transaction_id=104,
        )

        with self.assertRaisesRegex(ValueError, "0 이상"):
            self.repository.update_question_step(chat_session, -1)

        self.assertEqual(chat_session.question_step, 0)

    def test_records_url_sent_metadata(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-URL-SENT",
            transaction_id=105,
        )
        sent_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

        self.repository.record_url_sent(
            chat_session,
            notified_email="customer@example.com",
            email_sent_at=sent_at,
        )
        self.session.flush()

        self.assertEqual(chat_session.status, ChatSessionStatus.URL_SENT.value)
        self.assertEqual(chat_session.notified_email, "customer@example.com")
        self.assertEqual(chat_session.email_sent_at, sent_at)

    def test_marks_session_completed(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-DONE",
            transaction_id=106,
        )
        completed_at = datetime(2026, 8, 15, 13, 0, tzinfo=UTC)

        self.repository.mark_completed(
            chat_session,
            completed_at=completed_at,
        )
        self.session.flush()

        self.assertEqual(chat_session.status, ChatSessionStatus.DONE.value)
        self.assertEqual(chat_session.completed_at, completed_at)


if __name__ == "__main__":
    unittest.main()
