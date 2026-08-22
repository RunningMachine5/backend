import unittest
from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatDiscriminationAction as ChatDiscriminationActionModel,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
)
from app.data.model.transaction import Transaction
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.dto.chatbot import (
    AnswerQualityVerdict,
    ChatDiscriminationAction,
    DiscriminationQuestionId,
    ExtractedGuideSearchQuery,
)
from app.pipelines.customer_chatbot_pipeline import (
    ChatTurnRejectedError,
    ChatTurnResult,
    ChatTurnStreamEvent,
    CustomerChatbotPipeline,
)
from app.services.chatbot.answer_analyzer import AnswerAnalysisOutcome
from app.services.chatbot.guide_responder import GuideResponse


class FakeAnswerAnalyzer:
    def __init__(self, verdict=AnswerQualityVerdict.SUFFICIENT) -> None:
        self.verdict = verdict
        self.calls = []

    def analyze(self, *, question_text, customer_answer):
        self.calls.append((question_text, customer_answer))
        queries = ()
        if self.verdict is AnswerQualityVerdict.SUFFICIENT:
            queries = (
                ExtractedGuideSearchQuery(
                    title="악성 앱 삭제",
                    search_query="악성 앱 삭제 방법",
                    evidence=customer_answer,
                ),
            )
        return AnswerAnalysisOutcome(
            quality_verdict=self.verdict,
            guide_search_queries=queries,
        )


class FakeGuideResponder:
    def __init__(self) -> None:
        self.calls = []

    def respond(self, *, guide_search_queries, session, on_snapshot=None):
        queries = list(guide_search_queries)
        self.calls.append(queries)
        if on_snapshot is not None:
            on_snapshot("가이드 생성 중")
        return GuideResponse(message_text="■ 즉시 대응\n안내 본문")


class CustomerChatbotPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        for model in (
            Transaction,
            ChatSession,
            ChatMessage,
            ChatAnswer,
            ChatGuideSearchQuery,
            ChatDiscriminationActionModel,
        ):
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.transaction = Transaction(
            customer_id=None,
            source_account_number="source-0001",
            recipient_account_number="recipient-0001",
            transaction_datetime=datetime(2026, 8, 15, 14, 3, tzinfo=UTC),
            transaction_amount=-1_234_000,
            channel="mobile",
            type_general_automatic="general",
            access_medium="a",
            num_connection_failure=0,
            rooting_jailbreak_indicator=False,
            mobile_roaming_indicator=False,
            vpn_indicator=False,
            flag_terminal_malicious_behavior_1=False,
            flag_terminal_malicious_behavior_2=False,
            flag_terminal_malicious_behavior_3=False,
            flag_terminal_malicious_behavior_5=False,
            flag_terminal_malicious_behavior_6=False,
        )
        self.session.add(self.transaction)
        self.session.commit()
        self.analyzer = FakeAnswerAnalyzer()
        self.responder = FakeGuideResponder()
        self.chat_session = self._new_chat_session(0.80, 0.40)
        self.checkpointer = InMemorySaver()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _new_chat_session(self, primary_score, secondary_score, *, suffix="MAIN"):
        chat_session = ChatSession(
            chat_session_id=f"CHAT-{suffix}",
            transaction_id=self.transaction.id,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
            top_fraud_type_scores={
                VOICE_PHISHING: primary_score,
                MESSENGER_PHISHING: secondary_score,
            },
        )
        self.session.add(chat_session)
        self.session.commit()
        return chat_session

    def _pipeline(self, chat_session=None):
        return CustomerChatbotPipeline(
            session=self.session,
            chat_session=chat_session or self.chat_session,
            answer_analyzer=self.analyzer,
            guide_responder=self.responder,
            checkpointer=self.checkpointer,
        )

    @staticmethod
    def _consume(stream):
        items = list(stream)
        return [item for item in items if isinstance(item, ChatTurnStreamEvent)], items[-1]

    def _answer(self, pipeline, action, question_id, request_id):
        return self._consume(
            pipeline.handle_discrimination_action_stream(
                action=action,
                question_id=question_id,
                request_id=request_id,
            )
        )

    def test_entry_starts_discrimination_without_old_start_button(self) -> None:
        result = self._pipeline().start_discrimination()

        self.assertEqual(result.status, ChatSessionStatus.IN_PROGRESS)
        self.assertEqual(result.conversation_phase, "DISCRIMINATION")
        self.assertEqual(result.question_id, "OWNERSHIP")
        self.assertIn("본인이 한 거래가 맞나요?", result.messages)

    def test_score_margin_equal_to_point_fifteen_is_confident(self) -> None:
        self.chat_session.top_fraud_type_scores = {
            VOICE_PHISHING: 0.70,
            MESSENGER_PHISHING: 0.55,
        }
        self.session.commit()
        pipeline = self._pipeline()
        pipeline.start_discrimination()

        events, result = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "boundary-1",
        )

        self.assertEqual(events[0].event, "fraud_type_confirmed")
        self.assertEqual(result.confirmed_fraud_type, VOICE_PHISHING)

    def test_popup_is_emitted_before_rag_snapshot_without_answer_analyzer(self) -> None:
        pipeline = self._pipeline()
        pipeline.start_discrimination()

        events, result = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "confirm-1",
        )

        self.assertEqual(
            [event.event for event in events],
            ["fraud_type_confirmed", "chat_message_snapshot"],
        )
        self.assertEqual(self.analyzer.calls, [])
        self.assertEqual(result.conversation_phase, "FREE_CHAT")
        self.assertEqual(result.confirmed_fraud_type, VOICE_PHISHING)
        self.assertIn("보이스피싱이 의심돼요", events[0].data["message"])
        self.assertIn("모르는 사람의 금융 관련 전화", self.responder.calls[0][0].search_query)

    def test_ambiguous_no_no_from_owned_transaction_returns_normal_guide(self) -> None:
        self.chat_session.top_fraud_type_scores = {
            VOICE_PHISHING: 0.60,
            MESSENGER_PHISHING: 0.50,
        }
        self.session.commit()
        pipeline = self._pipeline()
        pipeline.start_discrimination()
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_YES,
            DiscriminationQuestionId.OWNERSHIP,
            "normal-1",
        )
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.PRIMARY_CHECK,
            "normal-2",
        )
        _, result = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.SECONDARY_CHECK,
            "normal-3",
        )

        self.assertEqual(result.conversation_phase, "NORMAL_GUIDE")
        self.assertEqual(result.status, ChatSessionStatus.DONE)
        self.assertIn("1599-9999", result.messages[0])

    def test_ambiguous_no_no_from_unowned_transaction_requests_handoff(self) -> None:
        self.chat_session.top_fraud_type_scores = {
            VOICE_PHISHING: 0.60,
            MESSENGER_PHISHING: 0.50,
        }
        self.session.commit()
        pipeline = self._pipeline()
        pipeline.start_discrimination()
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "handoff-1",
        )
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.PRIMARY_CHECK,
            "handoff-2",
        )
        _, result = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.SECONDARY_CHECK,
            "handoff-3",
        )

        self.assertEqual(result.conversation_phase, "HANDOFF_PENDING")
        self.assertEqual(result.status, ChatSessionStatus.HANDOFF_REQUESTED)
        self.assertIn("그동안 저에게 질문", result.messages[-1])

        _, free_chat_result = self._consume(
            pipeline.handle_message_stream("악성 앱은 어떻게 지워요?")
        )
        self.assertEqual(free_chat_result.status, ChatSessionStatus.HANDOFF_REQUESTED)
        self.assertTrue(self.analyzer.calls)

    def test_rejects_free_text_during_discrimination_and_stale_quick_reply(self) -> None:
        pipeline = self._pipeline()
        pipeline.start_discrimination()

        with self.assertRaisesRegex(ChatTurnRejectedError, "퀵리플라이"):
            pipeline.handle_message_stream("네")
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_YES,
            DiscriminationQuestionId.OWNERSHIP,
            "stale-1",
        )
        with self.assertRaisesRegex(ChatTurnRejectedError, "늦은"):
            pipeline.handle_discrimination_action_stream(
                action=ChatDiscriminationAction.ANSWER_YES,
                question_id=DiscriminationQuestionId.OWNERSHIP,
                request_id="stale-2",
            )

    def test_rejects_discrimination_action_in_free_chat(self) -> None:
        pipeline = self._pipeline()
        pipeline.start_discrimination()
        self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "free-1",
        )

        with self.assertRaisesRegex(ChatTurnRejectedError, "DISCRIMINATION"):
            pipeline.handle_discrimination_action_stream(
                action=ChatDiscriminationAction.ANSWER_YES,
                question_id=DiscriminationQuestionId.PRIMARY_CHECK,
                request_id="free-2",
            )

    def test_same_request_id_replays_without_duplicate_messages_or_rag(self) -> None:
        pipeline = self._pipeline()
        pipeline.start_discrimination()
        _, first = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "same-request",
        )
        message_count = self.session.exec(
            select(func.count()).select_from(ChatMessage)
        ).one()
        _, replay = self._answer(
            pipeline,
            ChatDiscriminationAction.ANSWER_NO,
            DiscriminationQuestionId.OWNERSHIP,
            "same-request",
        )

        self.assertEqual(replay, first)
        self.assertEqual(len(self.responder.calls), 1)
        self.assertEqual(
            self.session.exec(select(func.count()).select_from(ChatMessage)).one(),
            message_count,
        )
        self.assertEqual(
            self.session.exec(
                select(func.count()).select_from(ChatDiscriminationActionModel)
            ).one(),
            1,
        )


if __name__ == "__main__":
    unittest.main()
