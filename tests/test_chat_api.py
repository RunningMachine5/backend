"""고객 대응 챗봇 API(PRD 2.2~2.7)의 요청 경계 검증.

라우터가 세션을 찾고, 상태에 맞지 않는 입력을 막고, 파이프라인 결과를 응답 계약으로
옮기는 것까지만 본다. 그래프 내부 분기는 tests/test_chatbot_pipeline.py 가 맡는다.

LLM 은 부르지 않는다 — CI 가 ``OPENAI_API_KEY=test-only-key`` 로 돌기 때문에 실호출이
있으면 깨진다. 평가 LLM 이 필요한 경로는 파이프라인이 지연 생성하는 자리를 대역으로 바꾼다.
"""

import json
import os
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.chatbot import (
    ChatAnswer,
    ChatFraudCircumstance,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.domain.fraud_circumstance_codes import INCOMING_FUNDS_FORWARDED
from app.domain.fraud_type_codes import (
    FINAL_FRAUD_TYPE_CODES,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
    get_fraud_type_display_name,
)
from app.dto.chatbot import (
    AnswerQualityVerdict,
    ExtractedGuideSearchQuery,
    FraudCircumstanceExtractionTask,
    GuideSearchQueryExtractionResult,
)
from app.services.chatbot.answer_evaluator import AnswerEvaluationOutcome
from app.services.chatbot.chat_score_event_broker import chat_score_event_broker
from app.services.chatbot.chat_scoring import score_chat_fraud_circumstances
from app.services.chatbot.fraud_circumstance_task_runner import (
    get_fraud_circumstance_task_runner,
)
from app.services.chatbot.guide_responder import GuideResponse
from app.services.chatbot.messages import (
    END_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_MESSAGE,
)
from app.services.chatbot.questions import GREETING
from main import app


NOW = datetime(2026, 8, 16, 14, 3, tzinfo=UTC)
BIRTH_YEAR = "1958"


class _FakeEvaluator:
    """평가 LLM 자리에 끼워 넣는 대역. 판정을 고정한다."""

    def __init__(self, verdict: AnswerQualityVerdict) -> None:
        self.verdict = verdict

    def evaluate(
        self,
        *,
        question_text: str,
        customer_answer: str,
    ) -> AnswerEvaluationOutcome:
        return AnswerEvaluationOutcome(quality_verdict=self.verdict)


class _FakeGuideSearchQueryExtractor:
    """가이드 분해 LLM 자리에 끼워 넣는 대역. 분해 결과 0건을 돌려준다."""

    def extract(self, *, user_answers: str) -> GuideSearchQueryExtractionResult:
        return GuideSearchQueryExtractionResult(guide_search_queries=[])


class _FakeStreamingGuideSearchQueryExtractor:
    def extract(self, *, user_answers: str) -> GuideSearchQueryExtractionResult:
        return GuideSearchQueryExtractionResult(
            guide_search_queries=[
                ExtractedGuideSearchQuery(
                    title="의심스러운 링크",
                    search_query="의심스러운 링크 확인 방법",
                    evidence=user_answers,
                )
            ]
        )


class _FakeStreamingGuideResponder:
    def respond(self, *, guide_search_queries, session, on_snapshot=None):
        snapshots = [
            "■ 의심스러운 링크\n공식",
            "■ 의심스러운 링크\n공식 금융회사에 확인해 주세요.",
        ]
        if on_snapshot is not None:
            for snapshot in snapshots:
                on_snapshot(snapshot)
        return GuideResponse(message_text=snapshots[-1])


class _FailingEvaluator:
    def evaluate(self, *, question_text: str, customer_answer: str):
        raise RuntimeError("unexpected evaluator failure")


def _evaluating(verdict: AnswerQualityVerdict):
    """파이프라인이 지연 생성하는 ``AnswerEvaluator`` 를 대역으로 바꾼다."""

    return patch(
        "app.pipelines.customer_chatbot_pipeline.AnswerEvaluator",
        lambda: _FakeEvaluator(verdict),
    )


def _extracting_no_guide_query():
    """SUFFICIENT 턴이 부르는 가이드 분해 LLM 을 대역으로 바꾼다."""

    return patch(
        "app.pipelines.customer_chatbot_pipeline.GuideSearchQueryExtractor",
        _FakeGuideSearchQueryExtractor,
    )


class ChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Customer.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        ChatSession.__table__.create(self.engine)
        ChatMessage.__table__.create(self.engine)
        ChatAnswer.__table__.create(self.engine)
        ChatGuideSearchQuery.__table__.create(self.engine)
        ChatFraudCircumstance.__table__.create(self.engine)
        FraudTypeScoreAfterChat.__table__.create(self.engine)
        self.session = Session(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session

        # 사기 정황 추출은 응답 뒤 백그라운드로 도는 작업이라 실행하지 않고
        # 예약된 작업만 모은다(실행 자체는 tests/test_chatbot_fraud_circumstance_task.py).
        self.scheduled_extractions: list[FraudCircumstanceExtractionTask] = []
        app.dependency_overrides[get_fraud_circumstance_task_runner] = (
            lambda: self.scheduled_extractions.append
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.session.close()
        self.engine.dispose()

    # ------------------------------------------------------------------
    # 픽스처
    # ------------------------------------------------------------------

    def _seed_session(
        self,
        *,
        status: ChatSessionStatus = ChatSessionStatus.URL_SENT,
        question_step: int = 0,
        is_older: bool = False,
        customer_email: str | None = "hong@example.com",
    ) -> ChatSession:
        """거래·고객과 함께 세션 하나를 만든다.

        세션 id 는 매번 새로 뽑는다. 파이프라인의 체크포인터가 프로세스 전역이라
        같은 id 를 재사용하면 앞 테스트의 진행 상태를 물려받는다.
        """

        customer = Customer(
            id=1,
            name="홍길동",
            birth_date=date(int(BIRTH_YEAR), 3, 1),
            gender="male",
            identification_number="580301-1234567",
            email=customer_email,
            registration_datetime=NOW,
            credit_rating=3,
            loan_type="a",
        )
        transaction = Transaction(
            customer_id=1,
            source_account_number="source-0001",
            recipient_account_number="recipient-0001",
            transaction_datetime=NOW,
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
        self.session.add(customer)
        self.session.add(transaction)
        self.session.commit()

        chat_session = ChatSession(
            chat_session_id=f"CHAT-TEST-{uuid4().hex[:8].upper()}",
            transaction_id=transaction.id,
            status=status.value,
            is_older=is_older,
            question_step=question_step,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )
        self.session.add(chat_session)
        self.session.commit()
        return chat_session

    def _verify(self, chat_session_id: str, birth_year: str = BIRTH_YEAR):
        return self.client.post(
            f"/chat/{chat_session_id}/verify",
            json={"birth_year": birth_year},
        )

    def _action(self, chat_session_id: str, action: str):
        return self.client.post(
            f"/chat/{chat_session_id}/actions",
            json={"action": action},
        )

    def _send(self, chat_session_id: str, message_text: str = "모르는 번호로 전화가 왔어요"):
        return self.client.post(
            f"/chat/{chat_session_id}/messages",
            json={"message_text": message_text},
        )

    def _messages(self, chat_session_id: str) -> list[ChatMessage]:
        return list(
            self.session.exec(
                select(ChatMessage)
                .where(ChatMessage.chat_session_id == chat_session_id)
                .order_by(ChatMessage.message_id)
            ).all()
        )

    def _sse_events(self, response) -> list[tuple[str, dict]]:
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        events = []
        normalized = response.text.replace("\r\n", "\n")
        for block in normalized.split("\n\n"):
            if not block.strip() or block.startswith(":"):
                continue
            event = "message"
            data_lines = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").lstrip())
            events.append((event, json.loads("\n".join(data_lines))))
        return events

    def _completed_data(self, response) -> dict:
        completed = [
            data
            for event, data in self._sse_events(response)
            if event == "chat_turn_completed"
        ]
        self.assertEqual(len(completed), 1, response.text)
        return completed[0]

    # -- 2.2 본인인증과 첫 진입 ---------------------------------------

    def test_verify_returns_initial_notification_on_first_entry(self) -> None:
        chat_session = self._seed_session(is_older=True)

        response = self._verify(chat_session.chat_session_id)

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.URL_SENT.value)
        self.assertTrue(data["is_older"])
        self.assertEqual(data["question_step"], 0)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["sender_type"], "AI")
        self.assertIn("2026-08-16 14:03 1,234,000원 출금", data["messages"][0]["message_text"])

    def test_verify_does_not_repeat_notification_on_reentry(self) -> None:
        chat_session = self._seed_session()

        self._verify(chat_session.chat_session_id)
        response = self._verify(chat_session.chat_session_id)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["messages"]), 1)
        self.assertEqual(len(self._messages(chat_session.chat_session_id)), 1)

    def test_verify_rejects_wrong_birth_year(self) -> None:
        chat_session = self._seed_session()

        response = self._verify(chat_session.chat_session_id, birth_year="1990")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "HTTP_401")
        # 인증 전에는 최초 알림도 만들지 않는다.
        self.assertEqual(self._messages(chat_session.chat_session_id), [])

    def test_verify_rejects_malformed_birth_year(self) -> None:
        chat_session = self._seed_session()

        response = self._verify(chat_session.chat_session_id, birth_year="58")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_unknown_session_is_not_found(self) -> None:
        self.assertEqual(self._verify("CHAT-NOPE").status_code, 404)
        self.assertEqual(self.client.get("/chat/CHAT-NOPE").status_code, 404)
        self.assertEqual(self._action("CHAT-NOPE", "START_CHAT").status_code, 404)
        self.assertEqual(self._send("CHAT-NOPE").status_code, 404)

    def test_get_session_returns_history_without_creating_notification(self) -> None:
        chat_session = self._seed_session()

        response = self.client.get(f"/chat/{chat_session.chat_session_id}")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["chat_session_id"], chat_session.chat_session_id)
        self.assertEqual(data["transaction_id"], chat_session.transaction_id)
        self.assertEqual(data["messages"], [])
        self.assertEqual(self._messages(chat_session.chat_session_id), [])

    # -- 2.3 버튼 3종 --------------------------------------------------

    def test_start_chat_button_moves_to_in_progress_with_first_question(self) -> None:
        chat_session = self._seed_session()

        response = self._action(chat_session.chat_session_id, "START_CHAT")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.IN_PROGRESS.value)
        self.assertEqual(data["question_step"], 1)
        self.assertEqual(len(data["messages"]), 1)
        self.assertIn(GREETING, data["messages"][0])

    def test_handoff_button_transitions_to_handoff_requested(self) -> None:
        chat_session = self._seed_session()

        response = self._action(chat_session.chat_session_id, "REQUEST_HANDOFF")

        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.HANDOFF_REQUESTED.value)
        self.assertEqual(data["messages"], [HANDOFF_WAITING_MESSAGE])

    def test_end_button_completes_session(self) -> None:
        chat_session = self._seed_session()

        response = self._action(chat_session.chat_session_id, "END_CHAT")

        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.DONE.value)
        self.assertEqual(data["messages"], [END_CHAT_MESSAGE])

    def test_button_is_rejected_once_chat_started(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        response = self._action(chat_session.chat_session_id, "START_CHAT")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "HTTP_409")

    def test_unknown_button_action_is_rejected_by_schema(self) -> None:
        chat_session = self._seed_session()

        response = self._action(chat_session.chat_session_id, "GO_HOME")

        self.assertEqual(response.status_code, 422)

    # -- 2.4~2.6 고객 답변 --------------------------------------------

    def test_message_is_rejected_before_chat_starts(self) -> None:
        chat_session = self._seed_session()

        response = self._send(chat_session.chat_session_id)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._messages(chat_session.chat_session_id), [])

    def test_message_is_rejected_when_no_question_is_pending(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=0,
        )

        response = self._send(chat_session.chat_session_id)

        self.assertEqual(response.status_code, 409)

    def test_empty_message_is_rejected_by_schema(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        response = self._send(chat_session.chat_session_id, message_text="")

        self.assertEqual(response.status_code, 422)

    def test_vague_answer_reasks_without_advancing_step(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.TOO_VAGUE):
            response = self._send(chat_session.chat_session_id)

        self.assertEqual(response.status_code, 200, response.text)
        events = self._sse_events(response)
        self.assertEqual(
            [event for event, _ in events],
            ["chat_turn_started", "chat_turn_completed"],
        )
        data = events[-1][1]
        self.assertEqual(data["status"], ChatSessionStatus.IN_PROGRESS.value)
        self.assertEqual(data["question_step"], 1)
        self.assertEqual(data["messages"], [TOO_VAGUE_MESSAGE])
        # 고객 답변과 재질문 안내가 모두 이력에 남는다.
        senders = [message.sender_type for message in self._messages(chat_session.chat_session_id)]
        self.assertEqual(senders, ["HUMAN", "AI"])

    def test_want_end_answer_scores_and_completes_session(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.WANT_END):
            response = self._send(chat_session.chat_session_id, message_text="그만할래요")

        self.assertEqual(response.status_code, 200, response.text)
        data = self._completed_data(response)
        self.assertEqual(data["status"], ChatSessionStatus.DONE.value)
        self.assertEqual(data["messages"], [WANT_END_MESSAGE])
        scores = self.session.exec(select(FraudTypeScoreAfterChat)).all()
        self.assertEqual(len(scores), 1)

    def test_message_turn_publishes_score_event_to_subscriber(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )
        subscriber_queue = chat_score_event_broker.subscribe(
            chat_session.transaction_id
        )
        self.addCleanup(
            chat_score_event_broker.unsubscribe,
            chat_session.transaction_id,
            subscriber_queue,
        )

        with _evaluating(AnswerQualityVerdict.WANT_END):
            self._send(chat_session.chat_session_id, message_text="그만할래요")

        event = subscriber_queue.get_nowait()
        self.assertEqual(event.event, "chat_score_updated")
        self.assertEqual(
            event.data,
            {
                "transaction_id": chat_session.transaction_id,
                "type_scores": [
                    {
                        "type_code": type_code,
                        "display_name": get_fraud_type_display_name(type_code),
                        "score": 0,
                    }
                    for type_code in sorted(FINAL_FRAUD_TYPE_CODES)
                ],
            },
        )

    def test_sufficient_turn_schedules_background_extraction(self) -> None:
        """사기 정황 추출은 응답을 보낸 뒤 백그라운드로 돈다(PRD 2.6)."""

        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.SUFFICIENT), _extracting_no_guide_query():
            response = self._send(
                chat_session.chat_session_id,
                message_text="검찰이라고 전화가 왔어요",
            )

        self.assertEqual(response.status_code, 200, response.text)
        answer = self.session.exec(select(ChatAnswer)).one()
        self.assertEqual(
            self.scheduled_extractions,
            [
                FraudCircumstanceExtractionTask(
                    chat_session_id=chat_session.chat_session_id,
                    transaction_id=chat_session.transaction_id,
                    answer_id=answer.answer_id,
                    message_text="검찰이라고 전화가 왔어요",
                )
            ],
        )
        # 추출은 아직 돌지 않았으므로 정황 행도 없다.
        self.assertEqual(self.session.exec(select(ChatFraudCircumstance)).all(), [])

    def test_sufficient_answer_streams_cumulative_guide_snapshots(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with (
            _evaluating(AnswerQualityVerdict.SUFFICIENT),
            patch(
                "app.pipelines.customer_chatbot_pipeline.GuideSearchQueryExtractor",
                _FakeStreamingGuideSearchQueryExtractor,
            ),
            patch(
                "app.pipelines.customer_chatbot_pipeline.GuideResponder",
                _FakeStreamingGuideResponder,
            ),
        ):
            response = self._send(chat_session.chat_session_id)

        events = self._sse_events(response)
        self.assertEqual(
            [event for event, _ in events],
            [
                "chat_turn_started",
                "chat_message_snapshot",
                "chat_message_snapshot",
                "chat_turn_completed",
            ],
        )
        self.assertEqual(
            [data["message_text"] for event, data in events if event == "chat_message_snapshot"],
            [
                "■ 의심스러운 링크\n공식",
                "■ 의심스러운 링크\n공식 금융회사에 확인해 주세요.",
            ],
        )
        completed = events[-1][1]
        self.assertEqual(
            completed["messages"][0],
            "■ 의심스러운 링크\n공식 금융회사에 확인해 주세요.",
        )
        self.assertEqual(completed["question_step"], 2)

    def test_unexpected_stream_error_rolls_back_and_emits_error_event(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with patch(
            "app.pipelines.customer_chatbot_pipeline.AnswerEvaluator",
            _FailingEvaluator,
        ):
            response = self._send(chat_session.chat_session_id)

        self.assertEqual(
            [event for event, _ in self._sse_events(response)],
            ["chat_turn_started", "chat_turn_error"],
        )
        self.assertEqual(self._messages(chat_session.chat_session_id), [])
        self.assertEqual(self.session.exec(select(ChatAnswer)).all(), [])

    def test_non_sufficient_turn_schedules_nothing(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.TOO_VAGUE):
            self._send(chat_session.chat_session_id)

        self.assertEqual(self.scheduled_extractions, [])

    def test_message_turn_with_no_subscriber_does_not_raise(self) -> None:
        """구독자가 없는 거래에 대한 발행은 조용히 버려진다."""

        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.WANT_END):
            response = self._send(
                chat_session.chat_session_id, message_text="그만할래요"
            )

        self.assertEqual(response.status_code, 200, response.text)

    # -- 2.7 담당자 경로 -----------------------------------------------

    def test_transaction_status_lookup_returns_current_session(self) -> None:
        chat_session = self._seed_session(status=ChatSessionStatus.IN_PROGRESS)

        response = self.client.get(
            f"/transactions/{chat_session.transaction_id}/chat-session"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["data"],
            {
                "transaction_id": chat_session.transaction_id,
                "chat_session_id": chat_session.chat_session_id,
                "status": ChatSessionStatus.IN_PROGRESS.value,
            },
        )

    def test_transaction_without_session_returns_empty_values(self) -> None:
        response = self.client.get("/transactions/999/chat-session")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["data"],
            {
                "transaction_id": 999,
                "chat_session_id": None,
                "status": None,
            },
        )

    # -- 2.7 담당자 상세 조회 -------------------------------------------

    def _seed_transcript(self, chat_session: ChatSession) -> None:
        """대화 두 건을 넣는다(채점은 하지 않는다)."""

        self.session.add(
            ChatMessage(
                chat_session_id=chat_session.chat_session_id,
                sender_type="AI",
                message_text="어떤 일이 있으셨나요?",
                sent_at=NOW,
            )
        )
        self.session.add(
            ChatMessage(
                chat_session_id=chat_session.chat_session_id,
                sender_type="HUMAN",
                message_text="입금받은 돈을 다른 계좌로 다시 보냈어요",
                sent_at=NOW,
            )
        )
        self.session.commit()

    def _detail(self, transaction_id: int):
        return self.client.get(
            f"/transactions/{transaction_id}/chat-session/detail"
        )

    def test_detail_returns_transcript_and_scores(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.HANDOFF_REQUESTED,
            question_step=2,
            is_older=True,
        )
        self._seed_transcript(chat_session)
        self.session.add(
            FraudTypeScoreAfterChat(
                transaction_id=chat_session.transaction_id,
                chat_session_id=chat_session.chat_session_id,
                type_scores=score_chat_fraud_circumstances(
                    [INCOMING_FUNDS_FORWARDED]
                ),
                scored_at=NOW,
            )
        )
        self.session.commit()

        response = self._detail(chat_session.transaction_id)

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["chat_session_id"], chat_session.chat_session_id)
        self.assertEqual(
            data["status"],
            ChatSessionStatus.HANDOFF_REQUESTED.value,
        )
        self.assertEqual(len(data["messages"]), 2)
        self.assertTrue(data["type_scores"])
        # 화면이 쓰지 않는 값은 응답에 담지 않는다.
        self.assertEqual(
            set(data),
            {
                "transaction_id",
                "chat_session_id",
                "status",
                "completed_at",
                "messages",
                "type_scores",
            },
        )

    def test_detail_returns_messages_oldest_first(self) -> None:
        chat_session = self._seed_session()
        self._seed_transcript(chat_session)

        data = self._detail(chat_session.transaction_id).json()["data"]

        message_ids = [item["message_id"] for item in data["messages"]]
        self.assertEqual(message_ids, sorted(message_ids))
        self.assertEqual(
            [item["sender_type"] for item in data["messages"]],
            ["AI", "HUMAN"],
        )

    def test_detail_returns_every_fraud_type_score_highest_first(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.HANDOFF_REQUESTED
        )
        self.session.add(
            FraudTypeScoreAfterChat(
                transaction_id=chat_session.transaction_id,
                chat_session_id=chat_session.chat_session_id,
                type_scores=score_chat_fraud_circumstances(
                    [INCOMING_FUNDS_FORWARDED]
                ),
                scored_at=NOW,
            )
        )
        self.session.commit()

        data = self._detail(chat_session.transaction_id).json()["data"]

        scores = data["type_scores"]
        # 대표 유형을 고르지 않고 4개 유형을 전부 돌려준다.
        self.assertEqual(len(scores), len(FINAL_FRAUD_TYPE_CODES))
        self.assertEqual(
            [item["score"] for item in scores],
            sorted((item["score"] for item in scores), reverse=True),
        )
        self.assertEqual(scores[0]["type_code"], FRAUD_USED_ACCOUNT)
        self.assertEqual(scores[0]["display_name"], "사기이용계좌")

    def test_detail_before_scoring_returns_empty_scores(self) -> None:
        chat_session = self._seed_session(status=ChatSessionStatus.IN_PROGRESS)
        self._seed_transcript(chat_session)

        data = self._detail(chat_session.transaction_id).json()["data"]

        self.assertEqual(data["type_scores"], [])
        self.assertIsNone(data["completed_at"])
        # 채점 전이라도 대화 이력은 그대로 보인다.
        self.assertEqual(len(data["messages"]), 2)

    def test_detail_without_session_returns_empty_values(self) -> None:
        response = self._detail(999)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["data"],
            {
                "transaction_id": 999,
                "chat_session_id": None,
                "status": None,
                "completed_at": None,
                "messages": [],
                "type_scores": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
