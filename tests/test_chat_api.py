"""고객 대응 챗봇 API(PRD 2.2~2.7)의 요청 경계 검증.

라우터가 세션을 찾고, 상태에 맞지 않는 입력을 막고, 파이프라인 결과를 응답 계약으로
옮기는 것까지만 본다. 그래프 내부 분기는 tests/test_chatbot_pipeline.py 가 맡는다.

LLM 은 부르지 않는다 — CI 가 ``OPENAI_API_KEY=test-only-key`` 로 돌기 때문에 실호출이
있으면 깨진다. 평가 LLM 이 필요한 경로는 파이프라인이 지연 생성하는 자리를 대역으로 바꾼다.
"""

import asyncio
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

from app.api.chat import stream_chat_session_events
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
)
from app.dto.chatbot import AnswerQualityVerdict
from app.services.chatbot.answer_evaluator import AnswerEvaluationOutcome
from app.services.chatbot.chat_scoring import score_chat_fraud_circumstances
from app.services.chatbot.messages import (
    END_CHAT_MESSAGE,
    HANDOFF_WAITING_MESSAGE,
    TOO_VAGUE_MESSAGE,
    WANT_END_HANDOFF_MESSAGE,
)
from app.services.chatbot.questions import GREETING
from app.services.chatbot.session_event_broker import (
    CHAT_SESSION_STATUS_CHANGED_EVENT,
    chat_session_event_broker,
)
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


def _evaluating(verdict: AnswerQualityVerdict):
    """파이프라인이 지연 생성하는 ``AnswerEvaluator`` 를 대역으로 바꾼다."""

    return patch(
        "app.pipelines.customer_chatbot_pipeline.AnswerEvaluator",
        lambda: _FakeEvaluator(verdict),
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
        self.client = TestClient(app)
        # 라우터는 프로세스 전역 브로커를 쓴다. 발행 여부는 여기에 구독해 확인한다.
        self.events = chat_session_event_broker.subscribe()

    def tearDown(self) -> None:
        chat_session_event_broker.unsubscribe(self.events)
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
            id="CUST-1",
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
            customer_id="CUST-1",
            source_account_number="source-0001",
            recipient_account_number="recipient-0001",
            transaction_datetime=NOW,
            transaction_amount=-1_234_000,
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

    def _published_statuses(self) -> list[str]:
        statuses = []
        while not self.events.empty():
            statuses.append(self.events.get_nowait().payload.status)
        return statuses

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
        # 최초 알림은 상태를 바꾸지 않으므로 발행할 이벤트도 없다(PRD 2.3).
        self.assertEqual(self._published_statuses(), [])

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
        self.assertEqual(
            self._published_statuses(),
            [ChatSessionStatus.IN_PROGRESS.value],
        )

    def test_handoff_button_transitions_and_publishes_status(self) -> None:
        chat_session = self._seed_session()

        response = self._action(chat_session.chat_session_id, "REQUEST_HANDOFF")

        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.HANDOFF_REQUESTED.value)
        self.assertEqual(data["messages"], [HANDOFF_WAITING_MESSAGE])
        self.assertEqual(
            self._published_statuses(),
            [ChatSessionStatus.HANDOFF_REQUESTED.value],
        )

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
        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.IN_PROGRESS.value)
        self.assertEqual(data["question_step"], 1)
        self.assertEqual(data["messages"], [TOO_VAGUE_MESSAGE])
        # 고객 답변과 재질문 안내가 모두 이력에 남는다.
        senders = [message.sender_type for message in self._messages(chat_session.chat_session_id)]
        self.assertEqual(senders, ["HUMAN", "AI"])
        self.assertEqual(self._published_statuses(), [])

    def test_want_end_answer_scores_and_hands_off(self) -> None:
        chat_session = self._seed_session(
            status=ChatSessionStatus.IN_PROGRESS,
            question_step=1,
        )

        with _evaluating(AnswerQualityVerdict.WANT_END):
            response = self._send(chat_session.chat_session_id, message_text="그만할래요")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], ChatSessionStatus.HANDOFF_REQUESTED.value)
        self.assertEqual(data["messages"], [WANT_END_HANDOFF_MESSAGE])
        self.assertEqual(
            self._published_statuses(),
            [ChatSessionStatus.HANDOFF_REQUESTED.value],
        )
        scores = self.session.exec(select(FraudTypeScoreAfterChat)).all()
        self.assertEqual(len(scores), 1)

    # -- 2.7 담당자 경로 -----------------------------------------------

    def test_transaction_status_lookup_returns_current_session(self) -> None:
        chat_session = self._seed_session(status=ChatSessionStatus.IN_PROGRESS)

        response = self.client.get(
            f"/agent/transactions/{chat_session.transaction_id}/chat-session"
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
        response = self.client.get("/agent/transactions/999/chat-session")

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
            f"/agent/transactions/{transaction_id}/chat-session/detail"
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

    def test_event_stream_opens_with_retry_and_streams_status_change(self) -> None:
        """SSE 는 TestClient 로 열지 않는다.

        끝나지 않는 스트림이라 ``client.stream(...)`` 은 연결을 닫을 때 매달린다.
        라우터가 만든 응답 본문 이터레이터를 직접 읽어 프레임만 확인한다.
        """

        chat_session = self._seed_session()
        response = stream_chat_session_events()

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")

        async def read_frames() -> tuple[str, str]:
            iterator = response.body_iterator
            # 첫 조각을 받았다는 것은 구독이 끝났다는 뜻이다.
            # 그 뒤에 발행해야 이 연결로 흐른다.
            opening = await anext(iterator)
            chat_session_event_broker.publish_status_changed(chat_session)
            frame = await anext(iterator)
            await iterator.aclose()
            return opening, frame

        opening, frame = asyncio.run(read_frames())

        self.assertEqual(opening, "retry: 3000\n\n")
        # 프레임은 빈 줄로 끝나야 브라우저가 이벤트 하나로 끊어 읽는다.
        self.assertTrue(frame.endswith("\n\n"))
        event_line, data_line = frame.rstrip("\n").splitlines()
        self.assertEqual(event_line, f"event: {CHAT_SESSION_STATUS_CHANGED_EVENT}")
        self.assertEqual(
            json.loads(data_line.removeprefix("data: ")),
            {
                "transaction_id": chat_session.transaction_id,
                "chat_session_id": chat_session.chat_session_id,
                "status": ChatSessionStatus.URL_SENT.value,
            },
        )


if __name__ == "__main__":
    unittest.main()
