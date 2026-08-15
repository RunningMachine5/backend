import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import ChatSession
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


if __name__ == "__main__":
    unittest.main()
