"""턴 응답 뒤 백그라운드로 도는 사기 정황 추출·채점 검증(PRD 2.6).

추출 LLM 은 부르지 않는다 — CI 가 ``OPENAI_API_KEY=test-only-key`` 로 돌기 때문에
실호출이 있으면 깨진다. 추출기는 대역을 주입한다.
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatFraudCircumstance,
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.data.model.transaction import Transaction
from app.domain.fraud_circumstance_codes import (
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
)
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.dto.chatbot import (
    ExtractedFraudCircumstance,
    FraudCircumstanceExtractionResult,
    FraudCircumstanceExtractionTask,
)
from app.services.chatbot.chat_score_event_broker import chat_score_event_broker
from app.services.chatbot.extractors import FraudCircumstanceExtractionError
from app.services.chatbot.fraud_circumstance_task_runner import (
    run_fraud_circumstance_extraction,
)


RUNNER_MODULE = "app.services.chatbot.fraud_circumstance_task_runner"


class FakeFraudCircumstanceExtractor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or FraudCircumstanceExtractionResult(
            fraud_circumstances=[]
        )
        self.error = error
        self.calls: list[str] = []

    def extract(self, *, user_answers: str):
        self.calls.append(user_answers)
        if self.error is not None:
            raise self.error
        return self.result


def _extractor_with(*codes: str, evidence: str):
    return FakeFraudCircumstanceExtractor(
        FraudCircumstanceExtractionResult(
            fraud_circumstances=[
                ExtractedFraudCircumstance(type=code, evidence=evidence)
                for code in codes
            ]
        )
    )


class FraudCircumstanceTaskRunnerTest(unittest.TestCase):
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
        ChatFraudCircumstance.__table__.create(self.engine)
        FraudTypeScoreAfterChat.__table__.create(self.engine)
        self.session = Session(self.engine)

        # 백그라운드 작업은 자기 세션을 새로 연다. 그 세션이 테스트 DB 를 보게 한다.
        engine_patch = patch(f"{RUNNER_MODULE}.engine", self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)

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
            chat_session_id="CHAT-BACKGROUND",
            transaction_id=self.transaction.id,
            status=ChatSessionStatus.IN_PROGRESS.value,
            question_step=1,
            top_fraud_types=[VOICE_PHISHING, MESSENGER_PHISHING],
        )
        self.session.add(self.chat_session)
        self.session.commit()

        message = ChatMessage(
            chat_session_id=self.chat_session.chat_session_id,
            sender_type="HUMAN",
            message_text="검찰이라고 전화가 왔어요",
        )
        self.session.add(message)
        self.session.commit()

        self.answer = ChatAnswer(
            chat_session_id=self.chat_session.chat_session_id,
            message_id=message.message_id,
            question_step=1,
            attempt_no=1,
            quality_verdict="SUFFICIENT",
            is_adopted=True,
        )
        self.session.add(self.answer)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _task(self, message_text: str = "검찰이라고 전화가 왔어요"):
        return FraudCircumstanceExtractionTask(
            chat_session_id=self.chat_session.chat_session_id,
            transaction_id=self.transaction.id,
            answer_id=self.answer.answer_id,
            message_text=message_text,
        )

    def _circumstances(self) -> list[ChatFraudCircumstance]:
        return list(self.session.exec(select(ChatFraudCircumstance)).all())

    def _scores(self) -> list[FraudTypeScoreAfterChat]:
        self.session.expire_all()
        return list(self.session.exec(select(FraudTypeScoreAfterChat)).all())

    # ------------------------------------------------------------------

    def test_extraction_saves_circumstance_and_updates_score(self) -> None:
        extractor = _extractor_with(
            CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
            evidence="검찰이라고 전화가 왔어요",
        )

        run_fraud_circumstance_extraction(self._task(), extractor=extractor)

        self.assertEqual(extractor.calls, ["검찰이라고 전화가 왔어요"])
        stored = self._circumstances()
        self.assertEqual(len(stored), 1)
        self.assertEqual(
            stored[0].circumstance_code,
            CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
        )
        self.assertEqual(stored[0].source_answer_id, self.answer.answer_id)

        scores = self._scores()
        self.assertEqual(len(scores), 1)
        self.assertEqual(len(scores[0].type_scores), 4)
        self.assertGreater(scores[0].type_scores[VOICE_PHISHING], 0)

    def test_same_circumstance_twice_is_not_counted_twice(self) -> None:
        """세션당 enum 한 행 + 매번 전체 재계산이라 중복 가산되지 않는다."""

        for _ in range(2):
            run_fraud_circumstance_extraction(
                self._task(),
                extractor=_extractor_with(
                    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                    evidence="검찰이라고 전화가 왔어요",
                ),
            )

        self.assertEqual(len(self._circumstances()), 1)
        scores = self._scores()
        self.assertEqual(len(scores), 1)
        first_run_score = scores[0].type_scores[VOICE_PHISHING]

        run_fraud_circumstance_extraction(
            self._task(),
            extractor=_extractor_with(
                CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                evidence="검찰이라고 전화가 왔어요",
            ),
        )

        self.assertEqual(
            self._scores()[0].type_scores[VOICE_PHISHING],
            first_run_score,
        )

    def test_publishes_updated_score_to_sse_subscriber(self) -> None:
        subscriber_queue = chat_score_event_broker.subscribe(self.transaction.id)
        self.addCleanup(
            chat_score_event_broker.unsubscribe,
            self.transaction.id,
            subscriber_queue,
        )

        run_fraud_circumstance_extraction(
            self._task(),
            extractor=_extractor_with(
                CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                evidence="검찰이라고 전화가 왔어요",
            ),
        )

        event = subscriber_queue.get_nowait()
        self.assertEqual(event.event, "chat_score_updated")
        self.assertEqual(event.data["transaction_id"], self.transaction.id)
        published = {
            row["type_code"]: row["score"] for row in event.data["type_scores"]
        }
        # 커밋된 뒤에 발행하므로 저장된 점수와 같은 값이 나간다.
        self.assertEqual(published, self._scores()[0].type_scores)

    def test_extraction_failure_leaves_no_rows_and_does_not_raise(self) -> None:
        extractor = FakeFraudCircumstanceExtractor(
            error=FraudCircumstanceExtractionError("boom")
        )

        with self.assertLogs(RUNNER_MODULE, "WARNING"):
            run_fraud_circumstance_extraction(self._task(), extractor=extractor)

        self.assertEqual(self._circumstances(), [])
        self.assertEqual(self._scores(), [])

    def test_missing_chat_session_is_logged_and_skipped(self) -> None:
        task = FraudCircumstanceExtractionTask(
            chat_session_id="CHAT-NOT-EXIST",
            transaction_id=self.transaction.id,
            answer_id=self.answer.answer_id,
            message_text="검찰이라고 전화가 왔어요",
        )

        with self.assertLogs(RUNNER_MODULE, "WARNING"):
            run_fraud_circumstance_extraction(
                task,
                extractor=_extractor_with(
                    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                    evidence="검찰이라고 전화가 왔어요",
                ),
            )

        self.assertEqual(self._circumstances(), [])

    def test_unexpected_failure_does_not_escape(self) -> None:
        """턴 응답 뒤에 도는 작업이라 예외를 밖으로 올리지 않는다."""

        class ExplodingExtractor:
            def extract(self, *, user_answers: str):
                raise RuntimeError("boom")

        with self.assertLogs(RUNNER_MODULE, "ERROR"):
            result = run_fraud_circumstance_extraction(
                self._task(),
                extractor=ExplodingExtractor(),
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
