import unittest
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatCustomerAction,
    ChatFraudCircumstance,
    ChatMessage,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.domain.customer_action_codes import PHISHING_LINK_OPENED
from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
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
        ChatCustomerAction.__table__.create(self.engine)
        ChatFraudCircumstance.__table__.create(self.engine)
        FraudTypeScoreAfterChat.__table__.create(self.engine)
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

    def test_saves_customer_action_once_per_session(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-ACTION",
            transaction_id=112,
        )

        first_inserted = self.repository.add_customer_action(
            chat_session,
            action_code=PHISHING_LINK_OPENED,
            evidence="링크를 눌렀어요",
        )
        duplicate_inserted = self.repository.add_customer_action(
            chat_session,
            action_code=PHISHING_LINK_OPENED,
            evidence="같은 행동의 다른 근거",
        )

        self.assertIs(first_inserted, True)
        self.assertIs(duplicate_inserted, False)
        actions = list(
            self.session.exec(
                select(ChatCustomerAction).where(
                    ChatCustomerAction.chat_session_id
                    == chat_session.chat_session_id
                )
            ).all()
        )
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].evidence, "링크를 눌렀어요")

    def test_saves_fraud_circumstance_with_source_answer(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-CIRCUMSTANCE",
            transaction_id=113,
        )
        message = self.repository.add_message(
            chat_session,
            sender_type=ChatSenderType.HUMAN,
            message_text="재인증 링크라고 했어요",
        )
        answer = self.repository.add_answer(
            chat_session,
            message=message,
            question_step=1,
            attempt_no=1,
            quality_verdict=AnswerQualityVerdict.SUFFICIENT,
            is_adopted=True,
        )

        inserted = self.repository.add_fraud_circumstance(
            chat_session,
            circumstance_code=ACCOUNT_REAUTHENTICATION_PHISHING,
            evidence="재인증 링크라고 했어요",
            source_answer=answer,
        )
        duplicate_inserted = self.repository.add_fraud_circumstance(
            chat_session,
            circumstance_code=ACCOUNT_REAUTHENTICATION_PHISHING,
            evidence="중복 근거",
            source_answer=answer,
        )

        self.assertIs(inserted, True)
        self.assertIs(duplicate_inserted, False)
        circumstances = list(
            self.session.exec(select(ChatFraudCircumstance)).all()
        )
        self.assertEqual(len(circumstances), 1)
        self.assertEqual(circumstances[0].source_answer_id, answer.answer_id)

    def test_filters_invalid_extraction_codes(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-INVALID-EXTRACTION",
            transaction_id=114,
        )

        action_inserted = self.repository.add_customer_action(
            chat_session,
            action_code="unknown_customer_action",
            evidence="잘못된 행동",
        )
        circumstance_inserted = self.repository.add_fraud_circumstance(
            chat_session,
            circumstance_code="unknown_fraud_circumstance",
            evidence="잘못된 정황",
        )

        self.assertIs(action_inserted, False)
        self.assertIs(circumstance_inserted, False)
        action_count = self.session.exec(
            select(func.count()).select_from(ChatCustomerAction)
        ).one()
        circumstance_count = self.session.exec(
            select(func.count()).select_from(ChatFraudCircumstance)
        ).one()
        self.assertEqual(action_count, 0)
        self.assertEqual(circumstance_count, 0)

    def test_lists_all_fraud_circumstances_for_session(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-SCORE-SOURCE",
            transaction_id=115,
        )
        other_session = self.repository.create_or_get(
            chat_session_id="CHAT-SCORE-OTHER",
            transaction_id=116,
        )
        self.repository.add_fraud_circumstance(
            chat_session,
            circumstance_code=ACCOUNT_REAUTHENTICATION_PHISHING,
            evidence="재인증 링크라고 했어요",
        )
        self.repository.add_fraud_circumstance(
            chat_session,
            circumstance_code=CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
            evidence="검찰이 범죄에 연루됐다고 전화했어요",
        )
        self.repository.add_fraud_circumstance(
            other_session,
            circumstance_code=ACCOUNT_REAUTHENTICATION_PHISHING,
            evidence="다른 세션의 답변",
        )

        circumstances = self.repository.list_fraud_circumstances(chat_session)

        self.assertEqual(
            [item.circumstance_code for item in circumstances],
            [
                ACCOUNT_REAUTHENTICATION_PHISHING,
                CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
            ],
        )

    def test_saves_fraud_type_scores_once_per_transaction(self) -> None:
        chat_session = self.repository.create_or_get(
            chat_session_id="CHAT-SCORE",
            transaction_id=117,
        )
        type_scores = {
            VOICE_PHISHING: 3.0,
            MESSENGER_PHISHING: 0.0,
            ACCOUNT_TAKEOVER: 1.0,
            FRAUD_USED_ACCOUNT: 0.0,
        }

        first_inserted = self.repository.add_fraud_type_scores(
            chat_session,
            type_scores=type_scores,
        )
        duplicate_inserted = self.repository.add_fraud_type_scores(
            chat_session,
            type_scores={VOICE_PHISHING: 99.0},
        )

        self.assertIs(first_inserted, True)
        self.assertIs(duplicate_inserted, False)
        score = self.session.get(FraudTypeScoreAfterChat, 117)
        self.assertIsNotNone(score)
        assert score is not None
        self.assertEqual(score.chat_session_id, chat_session.chat_session_id)
        self.assertEqual(score.type_scores, type_scores)
        self.assertIsInstance(score.scored_at, datetime)

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
