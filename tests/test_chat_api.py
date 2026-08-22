import json
import os
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, func, select

from app.core.db import get_session
from app.data.model.chatbot import (
    ChatAnswer,
    ChatDiscriminationAction,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSession,
)
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.services.chatbot.guide_responder import GuideResponse
from main import app


NOW = datetime(2026, 8, 16, 14, 3, tzinfo=UTC)
BIRTH_YEAR = "1958"


class _FakeGuideResponder:
    def respond(self, *, guide_search_queries, session, on_snapshot=None):
        if on_snapshot is not None:
            on_snapshot("가이드 생성 중")
        return GuideResponse(message_text="■ 즉시 대응\n공식 안내")


class ChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        for model in (
            Customer,
            Transaction,
            ChatSession,
            ChatMessage,
            ChatAnswer,
            ChatGuideSearchQuery,
            ChatDiscriminationAction,
        ):
            model.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.client = TestClient(app)
        self.guide_patch = patch(
            "app.pipelines.customer_chatbot_pipeline.GuideResponder",
            _FakeGuideResponder,
        )
        self.guide_patch.start()

    def tearDown(self) -> None:
        self.guide_patch.stop()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _seed_session(self, *, margin=0.40) -> ChatSession:
        with Session(self.engine) as session:
            customer = Customer(
                name="홍길동",
                birth_date=date(int(BIRTH_YEAR), 3, 1),
                gender="male",
                identification_number=f"id-{uuid4().hex}",
                email="hong@example.com",
                registration_datetime=NOW,
                credit_rating=3,
                loan_type="a",
            )
            session.add(customer)
            session.flush()
            transaction = Transaction(
                customer_id=customer.id,
                source_account_number=f"source-{uuid4().hex}",
                recipient_account_number=f"recipient-{uuid4().hex}",
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
            session.add(transaction)
            session.flush()
            chat_session = ChatSession(
                chat_session_id=f"CHAT-TEST-{uuid4().hex[:8].upper()}",
                transaction_id=transaction.id,
                top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
                top_fraud_type_scores={
                    VOICE_PHISHING: 0.80,
                    MESSENGER_PHISHING: 0.80 - margin,
                },
            )
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def _verify(self, chat_session):
        return self.client.post(
            f"/chat/{chat_session.chat_session_id}/verify",
            json={"birth_year": BIRTH_YEAR},
        )

    def _action(self, chat_session, *, action, question_id, request_id):
        return self.client.post(
            f"/chat/{chat_session.chat_session_id}/discrimination-actions",
            json={
                "action": action,
                "question_id": question_id,
                "request_id": request_id,
            },
        )

    @staticmethod
    def _events(response):
        events = []
        current_event = None
        for line in response.text.splitlines():
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append(
                    (current_event, json.loads(line.removeprefix("data: ")))
                )
        return events

    def test_verify_returns_reconnectable_quick_reply_contract(self) -> None:
        chat_session = self._seed_session()

        response = self._verify(chat_session)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "IN_PROGRESS")
        self.assertEqual(data["conversation_phase"], "DISCRIMINATION")
        self.assertEqual(data["input_mode"], "QUICK_REPLY")
        self.assertEqual(data["question_id"], "OWNERSHIP")
        self.assertEqual(
            [item["action"] for item in data["quick_replies"]],
            ["ANSWER_YES", "ANSWER_NO"],
        )

    def test_confirmed_popup_precedes_rag_snapshot(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)

        response = self._action(
            chat_session,
            action="ANSWER_NO",
            question_id="OWNERSHIP",
            request_id="confirm-1",
        )

        events = self._events(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [event for event, _ in events],
            [
                "chat_turn_started",
                "fraud_type_confirmed",
                "chat_message_snapshot",
                "chat_turn_completed",
            ],
        )
        self.assertEqual(events[1][1]["confirmed_fraud_type"], VOICE_PHISHING)
        self.assertEqual(events[-1][1]["input_mode"], "FREE_TEXT")

    def test_reentry_does_not_return_popup_event(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)
        self._action(
            chat_session,
            action="ANSWER_NO",
            question_id="OWNERSHIP",
            request_id="confirm-2",
        )

        response = self.client.get(f"/chat/{chat_session.chat_session_id}")

        data = response.json()["data"]
        self.assertEqual(data["confirmed_fraud_type"], VOICE_PHISHING)
        self.assertEqual(data["input_mode"], "FREE_TEXT")
        self.assertNotIn("ui_events", data)

    def test_rejects_free_text_during_discrimination(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)

        response = self.client.post(
            f"/chat/{chat_session.chat_session_id}/messages",
            json={"message_text": "네"},
        )

        self.assertEqual(response.status_code, 409)

    def test_rejects_stale_question_and_action_after_free_chat(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)
        first = self._action(
            chat_session,
            action="ANSWER_YES",
            question_id="OWNERSHIP",
            request_id="step-1",
        )
        self.assertEqual(first.status_code, 200)

        stale = self._action(
            chat_session,
            action="ANSWER_YES",
            question_id="OWNERSHIP",
            request_id="step-2",
        )
        self.assertEqual(stale.status_code, 409)

    def test_same_request_id_does_not_duplicate_messages(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)
        kwargs = {
            "action": "ANSWER_YES",
            "question_id": "OWNERSHIP",
            "request_id": "same-request",
        }
        first = self._action(chat_session, **kwargs)
        with Session(self.engine) as session:
            count_before = session.exec(
                select(func.count()).select_from(ChatMessage)
            ).one()
        second = self._action(chat_session, **kwargs)
        with Session(self.engine) as session:
            count_after = session.exec(
                select(func.count()).select_from(ChatMessage)
            ).one()
            action_count = session.exec(
                select(func.count()).select_from(ChatDiscriminationAction)
            ).one()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(count_after, count_before)
        self.assertEqual(action_count, 1)

    def test_detail_returns_confirmed_type_without_rescoring_scores(self) -> None:
        chat_session = self._seed_session()
        self._verify(chat_session)
        self._action(
            chat_session,
            action="ANSWER_NO",
            question_id="OWNERSHIP",
            request_id="detail-1",
        )
        with Session(self.engine) as session:
            transaction_id = session.get(
                ChatSession, chat_session.chat_session_id
            ).transaction_id

        response = self.client.get(
            f"/transactions/{transaction_id}/chat-session/detail"
        )

        data = response.json()["data"]
        self.assertEqual(data["confirmed_fraud_type"], VOICE_PHISHING)
        self.assertNotIn("type_scores", data)
        self.assertEqual(
            self.client.get(
                f"/transactions/{transaction_id}/chat-session/score-events"
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
