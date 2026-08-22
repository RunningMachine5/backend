import unittest
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatDiscriminationAction,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.dto.chatbot import AnswerQualityVerdict
from app.repositories.chat_session import ChatSessionRepository


class ChatSessionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ChatSession.__table__.create(self.engine)
        ChatMessage.__table__.create(self.engine)
        ChatAnswer.__table__.create(self.engine)
        ChatGuideSearchQuery.__table__.create(self.engine)
        ChatDiscriminationAction.__table__.create(self.engine)
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

        self.repository.set_session_complete(
            chat_session,
            completed_at=completed_at,
        )
        self.session.flush()

        self.assertEqual(chat_session.status, ChatSessionStatus.DONE.value)
        self.assertEqual(chat_session.completed_at, completed_at)

    def test_adds_message_and_updates_last_message(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-MESSAGE",
            transaction_id=107,
        )

        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="제가 직접 송금했어요",
        )
        self.session.flush()

        self.assertIsNotNone(message.message_id)
        self.assertEqual(message.sender_type, ChatSenderType.HUMAN.value)
        self.assertEqual(message.message_text, "제가 직접 송금했어요")
        self.assertIsInstance(message.sent_at, datetime)
        self.assertEqual(chat_session.last_message_id, message.message_id)

    def test_adds_answer_metadata(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-ANSWER",
            transaction_id=108,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="제가 직접 송금했어요",
        )

        answer = self.repository.add_answer(
            chat_session,
            message=message,
            question_step=1,
            attempt_no=1,
            quality_verdict=AnswerQualityVerdict.SUFFICIENT,
            is_adopted=True,
        )
        self.session.flush()

        self.assertEqual(answer.message_id, message.message_id)
        self.assertEqual(answer.question_step, 1)
        self.assertEqual(answer.attempt_no, 1)
        self.assertEqual(
            answer.quality_verdict,
            AnswerQualityVerdict.SUFFICIENT.value,
        )
        self.assertIsNone(answer.verdict_skip_reason)
        self.assertIs(answer.is_adopted, True)

    def test_records_verdict_skip_reason(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-SKIPPED-ANSWER",
            transaction_id=109,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="잘 모르겠어요",
        )

        answer = self.repository.add_answer(
            chat_session,
            message=message,
            question_step=1,
            attempt_no=3,
            verdict_skip_reason="MAX_RETRY_EXCEEDED",
            is_adopted=True,
        )
        self.session.flush()

        self.assertIsNone(answer.quality_verdict)
        self.assertEqual(answer.verdict_skip_reason, "MAX_RETRY_EXCEEDED")

    def test_database_rejects_removed_quality_verdicts(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-REMOVED-VERDICTS",
            transaction_id=116,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="답변",
        )
        self.session.commit()

        for verdict in ("NON_ANSWER", "REFUSAL"):
            with self.subTest(verdict=verdict), self.assertRaises(IntegrityError):
                self.session.add(
                    ChatAnswer(
                        chat_session_id=chat_session.chat_session_id,
                        question_step=1,
                        attempt_no=1,
                        message_id=message.message_id,
                        quality_verdict=verdict,
                    )
                )
                self.session.flush()
            self.session.rollback()

    def test_rejects_attempt_number_outside_contract(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-INVALID-ATTEMPT",
            transaction_id=110,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="답변",
        )

        with self.assertRaisesRegex(ValueError, "1 이상 3 이하"):
            self.repository.add_answer(
                chat_session,
                message=message,
                question_step=1,
                attempt_no=4,
            )

    def test_database_rejects_two_adopted_answers_for_same_question(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-UNIQUE-ADOPTED",
            transaction_id=111,
        )
        first_message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="첫 번째 답변",
        )
        self.repository.add_answer(
            chat_session,
            message=first_message,
            question_step=1,
            attempt_no=1,
            quality_verdict=AnswerQualityVerdict.TOO_VAGUE,
            is_adopted=True,
        )
        self.session.flush()

        second_message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="두 번째 답변",
        )
        self.repository.add_answer(
            chat_session,
            message=second_message,
            question_step=1,
            attempt_no=2,
            quality_verdict=AnswerQualityVerdict.SUFFICIENT,
            is_adopted=True,
        )

        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()

    def test_saves_guide_search_query_once_per_answer_position(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-NEED",
            transaction_id=112,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="모르는 사람에게 전화번호를 보냈어요",
        )
        answer = self.repository.add_answer(
            chat_session,
            message=message,
            question_step=1,
            attempt_no=1,
            quality_verdict=AnswerQualityVerdict.SUFFICIENT,
            is_adopted=True,
        )

        first_inserted = self.repository.add_guide_search_query(
            chat_session,
            position=1,
            title="전화번호 제공",
            search_query="  모르는 사람에게  전화번호를 제공한 경우 대응 방법 ",
            evidence="전화번호를 보냈어요",
            source_answer=answer,
        )
        duplicate_inserted = self.repository.add_guide_search_query(
            chat_session,
            position=1,
            title="중복 제목",
            search_query="다른 검색 질의",
            evidence="전화번호를 보냈어요",
            source_answer=answer,
        )

        self.assertIs(first_inserted, True)
        self.assertIs(duplicate_inserted, False)
        queries = list(self.session.exec(select(ChatGuideSearchQuery)).all())
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0].source_answer_id, answer.answer_id)
        self.assertEqual(
            queries[0].search_query,
            "모르는 사람에게 전화번호를 제공한 경우 대응 방법",
        )

    def test_saves_and_reads_discrimination_action_receipt(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-ACTION",
            transaction_id=117,
        )
        self.repository.add_discrimination_action(
            chat_session,
            request_id="request-1",
            question_id="OWNERSHIP",
            action="ANSWER_YES",
            response_payload={"status": "IN_PROGRESS"},
        )
        self.session.flush()

        receipt = self.repository.get_discrimination_action(
            chat_session,
            request_id="request-1",
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.action, "ANSWER_YES")
        self.assertEqual(receipt.response_payload, {"status": "IN_PROGRESS"})

    def test_gets_session_status_by_transaction(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-STATUS",
            transaction_id=118,
        )
        self.repository.update_status(
            chat_session,
            ChatSessionStatus.HANDOFF_REQUESTED,
        )

        self.assertEqual(
            self.repository.get_status_by_transaction(118),
            ChatSessionStatus.HANDOFF_REQUESTED,
        )
        self.assertIsNone(self.repository.get_status_by_transaction(999))


if __name__ == "__main__":
    unittest.main()
