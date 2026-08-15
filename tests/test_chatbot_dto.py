import unittest

from pydantic import ValidationError

from app.domain.customer_action_codes import PHISHING_LINK_OPENED
from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
)
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.dto.chatbot import (
    AnswerEvaluationResult,
    AnswerQualityVerdict,
    ChatSessionStatusChangedEventPayload,
    CreateChatRequest,
    CustomerActionExtractionResult,
    FraudCircumstanceExtractionResult,
)


class TestChatbotStructuredOutputDTO(unittest.TestCase):
    def test_accepts_customer_action_whitelist_value(self) -> None:
        result = CustomerActionExtractionResult.model_validate(
            {
                "customer_actions": [
                    {
                        "type": PHISHING_LINK_OPENED,
                        "evidence": "링크를 눌렀어요",
                    }
                ]
            }
        )

        self.assertEqual(result.customer_actions[0].type, PHISHING_LINK_OPENED)

    def test_preserves_customer_action_evidence_whitespace(self) -> None:
        evidence = " 링크를 눌렀어요 "

        result = CustomerActionExtractionResult.model_validate(
            {
                "customer_actions": [
                    {
                        "type": PHISHING_LINK_OPENED,
                        "evidence": evidence,
                    }
                ]
            }
        )

        self.assertEqual(result.customer_actions[0].evidence, evidence)

    def test_rejects_customer_action_outside_whitelist(self) -> None:
        with self.assertRaises(ValidationError):
            CustomerActionExtractionResult.model_validate(
                {
                    "customer_actions": [
                        {
                            "type": "unknown_customer_action",
                            "evidence": "임의의 행동을 했어요",
                        }
                    ]
                }
            )

    def test_accepts_fraud_circumstance_whitelist_value(self) -> None:
        result = FraudCircumstanceExtractionResult.model_validate(
            {
                "fraud_circumstances": [
                    {
                        "type": ACCOUNT_REAUTHENTICATION_PHISHING,
                        "evidence": "재인증 링크라고 했어요",
                    }
                ]
            }
        )

        self.assertEqual(
            result.fraud_circumstances[0].type,
            ACCOUNT_REAUTHENTICATION_PHISHING,
        )

    def test_rejects_fraud_circumstance_outside_whitelist(self) -> None:
        with self.assertRaises(ValidationError):
            FraudCircumstanceExtractionResult.model_validate(
                {
                    "fraud_circumstances": [
                        {
                            "type": "unknown_fraud_circumstance",
                            "evidence": "임의의 정황이 있었어요",
                        }
                    ]
                }
            )

    def test_rejects_answer_quality_verdict_outside_contract(self) -> None:
        with self.assertRaises(ValidationError):
            AnswerEvaluationResult.model_validate({"verdict": "UNKNOWN"})

        result = AnswerEvaluationResult.model_validate({"verdict": "SUFFICIENT"})
        self.assertEqual(result.verdict, AnswerQualityVerdict.SUFFICIENT)

    def test_validates_session_status_changed_event(self) -> None:
        event = ChatSessionStatusChangedEventPayload.model_validate(
            {
                "transaction_id": 123,
                "chat_session_id": "CHAT-123",
                "status": "HANDOFF_REQUESTED",
            }
        )

        self.assertEqual(event.transaction_id, 123)
        self.assertEqual(event.status, "HANDOFF_REQUESTED")

        with self.assertRaises(ValidationError):
            ChatSessionStatusChangedEventPayload.model_validate(
                {
                    "transaction_id": 123,
                    "chat_session_id": "CHAT-123",
                    "status": "UNKNOWN",
                }
            )


class TestCreateChatRequestTopFraudTypes(unittest.TestCase):
    """유형판별 질문(PRD 2.4)에 쓰는 상위 2개 사기유형 필드 계약을 검증한다."""

    def test_accepts_two_distinct_fraud_types(self) -> None:
        request = CreateChatRequest.model_validate(
            {
                "transaction_id": "tx-1",
                "top_fraud_types": [VOICE_PHISHING, MESSENGER_PHISHING],
            }
        )

        self.assertEqual(
            request.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

    def test_accepts_omitted_top_fraud_types(self) -> None:
        """룰 채점 실패 거래는 필드를 생략하고 일반 질문 폴백을 쓴다."""

        request = CreateChatRequest.model_validate({"transaction_id": "tx-1"})

        self.assertIsNone(request.top_fraud_types)

    def test_rejects_fraud_type_outside_whitelist(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": "tx-1",
                    "top_fraud_types": [VOICE_PHISHING, "UNKNOWN_TYPE"],
                }
            )

    def test_rejects_wrong_item_count(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": "tx-1",
                    "top_fraud_types": [VOICE_PHISHING],
                }
            )

        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": "tx-1",
                    "top_fraud_types": [
                        VOICE_PHISHING,
                        MESSENGER_PHISHING,
                        VOICE_PHISHING,
                    ],
                }
            )

    def test_rejects_duplicate_fraud_types(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": "tx-1",
                    "top_fraud_types": [VOICE_PHISHING, VOICE_PHISHING],
                }
            )


if __name__ == "__main__":
    unittest.main()
