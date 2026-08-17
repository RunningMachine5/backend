"""LangGraph 챗봇 파이프라인의 분기 검증.

LLM·RAG는 전부 모킹한다. CI가 OPENAI_API_KEY=test-only-key로 돌기 때문에
실호출이 있으면 깨진다.
"""

import unittest
from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatFraudCircumstance,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.data.model.transaction import Transaction
from app.domain.fraud_circumstance_codes import (
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
)
from app.domain.fraud_type_codes import (
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.dto.chatbot import (
    AnswerQualityVerdict,
    ChatButtonAction,
    ExtractedFraudCircumstance,
    ExtractedGuideSearchQuery,
    FraudCircumstanceExtractionResult,
    GuideSearchQueryExtractionResult,
)
from app.pipelines.customer_chatbot_pipeline import (
    ChatTurnRejectedError,
    CustomerChatbotPipeline,
)
from app.services.chatbot.answer_evaluator import AnswerEvaluationOutcome
from app.services.chatbot.extractors import (
    FraudCircumstanceExtractionError,
    GuideSearchQueryExtractionError,
)
from app.services.chatbot.guide_responder import GuideResponse
from app.services.chatbot.messages import (
    END_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    NEXT_QUESTION_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_HANDOFF_MESSAGE,
)
from app.services.chatbot.questions import (
    FOLLOW_UP_QUESTION,
    GREETING,
    TYPE_DISCRIMINATION_QUESTIONS,
)
from app.services.chatbot.session_event_broker import ChatSessionEventBroker


class FakeEvaluator:
    """판정을 미리 정해두고 순서대로 돌려준다."""

    def __init__(self, *verdicts: AnswerQualityVerdict) -> None:
        self.outcomes = [
            AnswerEvaluationOutcome(quality_verdict=verdict)
            for verdict in verdicts
        ]
        self.calls: list[tuple[str, str]] = []

    def evaluate(self, *, question_text: str, customer_answer: str):
        self.calls.append((question_text, customer_answer))
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]


class FakeGuideSearchQueryExtractor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or GuideSearchQueryExtractionResult(
            guide_search_queries=[]
        )
        self.error = error
        self.calls = 0

    def extract(self, *, user_answers: str):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeFraudCircumstanceExtractor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or FraudCircumstanceExtractionResult(
            fraud_circumstances=[]
        )
        self.error = error
        self.calls = 0

    def extract(self, *, user_answers: str):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeGuideResponder:
    def __init__(self, message_text: str = "■ 안내\n본문") -> None:
        self.message_text = message_text
        self.calls = 0

    def respond(self, *, guide_search_queries, session):
        self.calls += 1
        return GuideResponse(message_text=self.message_text)


class CustomerChatbotPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Transaction.__table__.create(self.engine)
        ChatSession.__table__.create(self.engine)
        ChatMessage.__table__.create(self.engine)
        ChatAnswer.__table__.create(self.engine)
        ChatGuideSearchQuery.__table__.create(self.engine)
        ChatFraudCircumstance.__table__.create(self.engine)
        FraudTypeScoreAfterChat.__table__.create(self.engine)
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

        self.chat_session = ChatSession(
            chat_session_id="CHAT-PIPELINE",
            transaction_id=self.transaction.id,
            status=ChatSessionStatus.URL_SENT.value,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )
        self.session.add(self.chat_session)
        self.session.commit()

        # 상태 변경 SSE 는 프로세스 전역 브로커 대신 테스트 전용 브로커로 받는다.
        self.event_broker = ChatSessionEventBroker()
        self.events = self.event_broker.subscribe()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _pipeline(self, **overrides) -> CustomerChatbotPipeline:
        """턴 사이 상태만 공유하고 테스트끼리는 격리된 체크포인터를 쓴다."""

        options = {
            "evaluator": FakeEvaluator(AnswerQualityVerdict.SUFFICIENT),
            "guide_search_query_extractor": FakeGuideSearchQueryExtractor(),
            "fraud_circumstance_extractor": FakeFraudCircumstanceExtractor(),
            "guide_responder": FakeGuideResponder(),
        }
        options.update(overrides)
        checkpointer = getattr(self, "_checkpointer", None)
        if checkpointer is None:
            checkpointer = InMemorySaver()
            self._checkpointer = checkpointer
        return CustomerChatbotPipeline(
            session=self.session,
            chat_session=self.chat_session,
            checkpointer=checkpointer,
            event_broker=self.event_broker,
            **options,
        )

    def _start_chat(self, **overrides) -> CustomerChatbotPipeline:
        """버튼으로 상담을 시작해 question_step 1까지 진행한 파이프라인."""

        pipeline = self._pipeline(**overrides)
        pipeline.handle_button(ChatButtonAction.START_CHAT)
        return pipeline

    def _drain_events(self) -> list:
        """이번 검증 이전에 쌓인 이벤트를 비운다."""

        drained = []
        while not self.events.empty():
            drained.append(self.events.get_nowait())
        return drained

    def _published_statuses(self) -> list[str]:
        return [event.payload.status for event in self._drain_events()]

    def _answers(self) -> list[ChatAnswer]:
        return list(
            self.session.exec(select(ChatAnswer).order_by(ChatAnswer.answer_id)).all()
        )

    # -- 2.3 최초 알림과 버튼 3종 -------------------------------------

    def test_initial_notification_renders_transaction_and_keeps_status(self) -> None:
        result = self._pipeline().send_initial_notification()

        self.assertEqual(len(result.messages), 1)
        self.assertIn("2026-08-15 14:03 1,234,000원 출금", result.messages[0])
        self.assertIn("처리 보류 중입니다", result.messages[0])
        self.assertEqual(result.status, ChatSessionStatus.URL_SENT)

    def test_start_chat_button_moves_to_in_progress_and_first_question(self) -> None:
        result = self._pipeline().handle_button(ChatButtonAction.START_CHAT)

        self.assertEqual(result.status, ChatSessionStatus.IN_PROGRESS)
        self.assertEqual(result.question_step, 1)
        # "챗봇 상담"은 별도 문구 없이 바로 첫 질문만 출력한다(B.2).
        self.assertEqual(len(result.messages), 1)
        self.assertIn(GREETING, result.messages[0])
        self.assertIn(
            TYPE_DISCRIMINATION_QUESTIONS[
                frozenset({VOICE_PHISHING, MESSENGER_PHISHING})
            ],
            result.messages[0],
        )

    def test_handoff_button_transitions_without_completed_at(self) -> None:
        result = self._pipeline().handle_button(ChatButtonAction.REQUEST_HANDOFF)

        self.assertEqual(result.status, ChatSessionStatus.HANDOFF_REQUESTED)
        self.assertEqual(result.messages, (HANDOFF_WAITING_MESSAGE,))
        self.assertIsNone(self.chat_session.completed_at)

    def test_end_button_completes_session(self) -> None:
        result = self._pipeline().handle_button(ChatButtonAction.END_CHAT)

        self.assertEqual(result.status, ChatSessionStatus.DONE)
        self.assertEqual(result.messages, (END_CHAT_MESSAGE,))
        self.assertIsNotNone(self.chat_session.completed_at)

    def test_button_is_rejected_after_chat_started(self) -> None:
        pipeline = self._start_chat()

        with self.assertRaises(ChatTurnRejectedError):
            pipeline.handle_button(ChatButtonAction.START_CHAT)

    def test_message_is_rejected_before_chat_started(self) -> None:
        with self.assertRaises(ChatTurnRejectedError):
            self._pipeline().handle_message("아무 말")

    def test_message_is_rejected_when_no_question_was_asked(self) -> None:
        self.chat_session.status = ChatSessionStatus.IN_PROGRESS.value
        self.chat_session.question_step = 0
        self.session.add(self.chat_session)
        self.session.commit()

        with self.assertRaisesRegex(
            ChatTurnRejectedError,
            "답변을 기다리는 질문이 없습니다",
        ):
            self._pipeline().handle_message("아무 말")

    # -- 2.4 판정별 분기 ----------------------------------------------

    def test_sufficient_runs_extraction_and_asks_next_question(self) -> None:
        guide_extractor = FakeGuideSearchQueryExtractor(
            GuideSearchQueryExtractionResult(
                guide_search_queries=[
                    ExtractedGuideSearchQuery(
                        title="의심스러운 링크를 열었을 때",
                        search_query="문자 링크를 클릭했을 때 어떻게 해야 하나요?",
                        evidence="문자로 온 링크를 눌렀어요",
                    )
                ]
            )
        )
        circumstance_extractor = FakeFraudCircumstanceExtractor(
            FraudCircumstanceExtractionResult(
                fraud_circumstances=[
                    ExtractedFraudCircumstance(
                        type=CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                        evidence="검찰이라고 전화가 왔어요",
                    )
                ]
            )
        )
        responder = FakeGuideResponder()
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.SUFFICIENT),
            guide_search_query_extractor=guide_extractor,
            fraud_circumstance_extractor=circumstance_extractor,
            guide_responder=responder,
        )

        result = pipeline.handle_message(
            "검찰이라고 전화가 왔어요 그리고 문자로 온 링크를 눌렀어요"
        )

        # 가이드 응답 → 다음 질문 순서로 나간다.
        self.assertEqual(
            result.messages,
            (responder.message_text, FOLLOW_UP_QUESTION),
        )
        self.assertEqual(result.question_step, 2)
        self.assertEqual(result.status, ChatSessionStatus.IN_PROGRESS)

        answer = self._answers()[0]
        self.assertEqual(answer.attempt_no, 1)
        self.assertEqual(answer.quality_verdict, AnswerQualityVerdict.SUFFICIENT)
        self.assertTrue(answer.is_adopted)

        stored_queries = self.session.exec(select(ChatGuideSearchQuery)).all()
        self.assertEqual(len(stored_queries), 1)
        stored_circumstances = self.session.exec(
            select(ChatFraudCircumstance)
        ).all()
        self.assertEqual(len(stored_circumstances), 1)

    def test_sufficient_sends_no_guide_message_when_no_search_query(self) -> None:
        responder = FakeGuideResponder()
        pipeline = self._start_chat(guide_responder=responder)

        result = pipeline.handle_message("특별히 한 건 없어요")

        # 분해 결과가 0개면 본문이 비므로 대응 가이드 메시지를 보내지 않는다.
        self.assertEqual(result.messages, (FOLLOW_UP_QUESTION,))
        self.assertEqual(responder.calls, 0)

    def test_extraction_failure_does_not_block_the_other_path(self) -> None:
        guide_extractor = FakeGuideSearchQueryExtractor(
            error=GuideSearchQueryExtractionError("boom")
        )
        circumstance_extractor = FakeFraudCircumstanceExtractor(
            FraudCircumstanceExtractionResult(
                fraud_circumstances=[
                    ExtractedFraudCircumstance(
                        type=CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                        evidence="검찰이라고 전화가 왔어요",
                    )
                ]
            )
        )
        pipeline = self._start_chat(
            guide_search_query_extractor=guide_extractor,
            fraud_circumstance_extractor=circumstance_extractor,
        )

        result = pipeline.handle_message("검찰이라고 전화가 왔어요")

        self.assertEqual(result.messages, (FOLLOW_UP_QUESTION,))
        self.assertEqual(
            len(self.session.exec(select(ChatFraudCircumstance)).all()),
            1,
        )

    def test_circumstance_failure_does_not_block_guide_response(self) -> None:
        guide_extractor = FakeGuideSearchQueryExtractor(
            GuideSearchQueryExtractionResult(
                guide_search_queries=[
                    ExtractedGuideSearchQuery(
                        title="의심스러운 링크를 열었을 때",
                        search_query="문자 링크를 클릭했을 때 어떻게 해야 하나요?",
                        evidence="문자로 온 링크를 눌렀어요",
                    )
                ]
            )
        )
        pipeline = self._start_chat(
            guide_search_query_extractor=guide_extractor,
            fraud_circumstance_extractor=FakeFraudCircumstanceExtractor(
                error=FraudCircumstanceExtractionError("boom")
            ),
        )

        result = pipeline.handle_message("문자로 온 링크를 눌렀어요")

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(
            len(self.session.exec(select(ChatGuideSearchQuery)).all()),
            1,
        )

    def test_too_vague_reasks_without_advancing_question_step(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.TOO_VAGUE)
        )

        result = pipeline.handle_message("그냥요")

        self.assertEqual(result.messages, (TOO_VAGUE_MESSAGE,))
        self.assertEqual(result.question_step, 1)
        self.assertFalse(self._answers()[0].is_adopted)

    def test_refusal_like_answer_uses_too_vague_reask(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.TOO_VAGUE)
        )

        result = pipeline.handle_message("말하기 싫어요")

        self.assertEqual(result.messages, (TOO_VAGUE_MESSAGE,))
        self.assertEqual(result.question_step, 1)

    def test_third_vague_answer_is_adopted_and_moves_on(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.TOO_VAGUE)
        )

        pipeline.handle_message("몰라요")
        pipeline.handle_message("그냥요")
        result = pipeline.handle_message("그렇다니까요")

        # 재질문 2회를 소진하면 전이 안내와 함께 다음 질문으로 넘어간다.
        self.assertEqual(
            result.messages,
            (NEXT_QUESTION_MESSAGE, FOLLOW_UP_QUESTION),
        )
        self.assertEqual(result.question_step, 2)

        answers = self._answers()
        self.assertEqual([answer.attempt_no for answer in answers], [1, 2, 3])
        self.assertEqual(
            [answer.is_adopted for answer in answers],
            [False, False, True],
        )

    def test_evaluator_failure_uses_separate_next_question_route(self) -> None:
        class FailingEvaluator:
            def evaluate(self, *, question_text: str, customer_answer: str):
                return AnswerEvaluationOutcome(
                    quality_verdict=None,
                    verdict_skip_reason="EVALUATOR_FAILED",
                )

        pipeline = self._start_chat(evaluator=FailingEvaluator())

        result = pipeline.handle_message("링크를 눌렀어요")

        self.assertEqual(
            result.messages,
            (NEXT_QUESTION_MESSAGE, FOLLOW_UP_QUESTION),
        )
        answer = self._answers()[0]
        self.assertIsNone(answer.quality_verdict)
        self.assertEqual(answer.verdict_skip_reason, "EVALUATOR_FAILED")
        self.assertFalse(answer.is_adopted)

    # -- 2.6 종료와 채점 집계 ------------------------------------------

    def test_want_end_scores_once_and_requests_handoff(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(
                AnswerQualityVerdict.SUFFICIENT,
                AnswerQualityVerdict.WANT_END,
            ),
            fraud_circumstance_extractor=FakeFraudCircumstanceExtractor(
                FraudCircumstanceExtractionResult(
                    fraud_circumstances=[
                        ExtractedFraudCircumstance(
                            type=CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                            evidence="검찰이라고 전화가 왔어요",
                        )
                    ]
                )
            ),
        )
        pipeline.handle_message("검찰이라고 전화가 왔어요")

        result = pipeline.handle_message("종료할게요")

        self.assertEqual(result.messages, (WANT_END_HANDOFF_MESSAGE,))
        self.assertEqual(result.status, ChatSessionStatus.HANDOFF_REQUESTED)
        self.assertIsNotNone(self.chat_session.completed_at)

        scores = self.session.exec(select(FraudTypeScoreAfterChat)).all()
        self.assertEqual(len(scores), 1)
        self.assertEqual(len(scores[0].type_scores), 4)
        # 집계는 전이보다 먼저 끝나야 한다(PRD 2.6).
        self.assertGreater(scores[0].type_scores[VOICE_PHISHING], 0)

    def test_want_end_with_no_circumstance_stores_zero_scores(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.WANT_END)
        )

        pipeline.handle_message("그만할래요")

        scores = self.session.exec(select(FraudTypeScoreAfterChat)).all()
        self.assertEqual(len(scores), 1)
        self.assertEqual(set(scores[0].type_scores.values()), {0})

    def test_want_end_does_not_adopt_the_answer(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.WANT_END)
        )

        pipeline.handle_message("그만할래요")

        self.assertFalse(self._answers()[0].is_adopted)

    # -- 2.5 0건은 상태를 전이시키지 않는다 -----------------------------

    def test_all_zero_hit_guide_response_keeps_status(self) -> None:
        guide_extractor = FakeGuideSearchQueryExtractor(
            GuideSearchQueryExtractionResult(
                guide_search_queries=[
                    ExtractedGuideSearchQuery(
                        title="신분증 사본을 전달했을 때",
                        search_query="신분증 사본을 보냈을 때 어떻게 해야 하나요?",
                        evidence="신분증 사진을 보냈어요",
                    )
                ]
            )
        )
        # 전체 0건이어도 GuideResponder는 B.5 문구로 채운 본문을 돌려준다.
        responder = FakeGuideResponder(
            "■ 신분증 사본을 전달했을 때\n"
            "말씀해주신 이 부분은 제가 안내해드릴 수 있는 자료를 찾지 못했어요."
        )
        pipeline = self._start_chat(
            guide_search_query_extractor=guide_extractor,
            guide_responder=responder,
        )

        # 상담 시작(IN_PROGRESS) 이벤트를 비우고 이번 턴만 본다.
        self._drain_events()

        result = pipeline.handle_message("신분증 사진을 보냈어요")

        self.assertEqual(result.status, ChatSessionStatus.IN_PROGRESS)
        self.assertEqual(result.question_step, 2)
        self.assertEqual(self._published_statuses(), [])

    # -- 2.7 상태 변경 SSE 발행 ----------------------------------------

    def test_button_transitions_publish_one_event_each(self) -> None:
        for action, expected in (
            (ChatButtonAction.START_CHAT, ChatSessionStatus.IN_PROGRESS),
            (ChatButtonAction.REQUEST_HANDOFF, ChatSessionStatus.HANDOFF_REQUESTED),
            (ChatButtonAction.END_CHAT, ChatSessionStatus.DONE),
        ):
            with self.subTest(action=action):
                self.setUp()

                self._pipeline().handle_button(action)

                published = self._drain_events()
                self.assertEqual(
                    [event.payload.status for event in published],
                    [expected.value],
                )
                self.assertEqual(
                    published[0].payload.transaction_id,
                    self.transaction.id,
                )

    def test_initial_notification_publishes_nothing(self) -> None:
        """상태가 그대로면 담당자 화면을 갱신할 것이 없다."""

        self._pipeline().send_initial_notification()

        self.assertEqual(self._published_statuses(), [])

    def test_reask_turn_publishes_nothing(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.TOO_VAGUE)
        )
        self._drain_events()

        pipeline.handle_message("몰라요")

        self.assertEqual(self._published_statuses(), [])

    def test_want_end_publishes_handoff_requested(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.WANT_END)
        )
        self._drain_events()

        pipeline.handle_message("그만할래요")

        self.assertEqual(
            self._published_statuses(),
            [ChatSessionStatus.HANDOFF_REQUESTED.value],
        )

    # -- 질문 진행 -----------------------------------------------------

    def test_question_step_two_and_beyond_repeat_follow_up(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.SUFFICIENT)
        )

        pipeline.handle_message("첫 답변")
        result = pipeline.handle_message("두 번째 답변")

        self.assertEqual(result.messages, (FOLLOW_UP_QUESTION,))
        self.assertEqual(result.question_step, 3)

    def test_general_fallback_question_without_top_fraud_types(self) -> None:
        self.chat_session.top_fraud_types = None
        self.session.add(self.chat_session)
        self.session.commit()

        result = self._pipeline().handle_button(ChatButtonAction.START_CHAT)

        self.assertIn(
            "본인이 직접 실행하거나 승인한 거래인지",
            result.messages[0],
        )

    def test_ai_and_human_messages_are_logged(self) -> None:
        pipeline = self._start_chat(
            evaluator=FakeEvaluator(AnswerQualityVerdict.TOO_VAGUE)
        )
        pipeline.handle_message("말하기 싫어요")

        messages = list(
            self.session.exec(select(ChatMessage).order_by(ChatMessage.message_id)).all()
        )
        self.assertEqual(
            [message.sender_type for message in messages],
            ["AI", "HUMAN", "AI"],
        )
        self.assertEqual(
            self.chat_session.last_message_id,
            messages[-1].message_id,
        )


if __name__ == "__main__":
    unittest.main()
