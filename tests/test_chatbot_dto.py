import unittest

from pydantic import ValidationError

from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
)
from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from app.dto.chatbot import (
    AnswerEvaluationResult,
    AnswerQualityVerdict,
    ChatSessionStatusChangedEventPayload,
    CreateChatRequest,
    FraudCircumstanceExtractionResult,
    GuideSearchQueryExtractionResult,
)


class TestChatbotStructuredOutputDTO(unittest.TestCase):
    def test_accepts_guide_search_query_without_action_whitelist(self) -> None:
        result = GuideSearchQueryExtractionResult.model_validate(
            {
                "guide_search_queries": [
                    {
                        "title": "전화번호 제공",
                        "search_query": "모르는 사람에게 전화번호를 제공한 경우 대응 방법",
                        "evidence": "링크를 눌렀어요",
                    }
                ]
            }
        )

        self.assertEqual(result.guide_search_queries[0].title, "전화번호 제공")

    def test_preserves_guide_search_query_evidence_whitespace(self) -> None:
        evidence = " 링크를 눌렀어요 "

        result = GuideSearchQueryExtractionResult.model_validate(
            {
                "guide_search_queries": [
                    {
                        "title": "의심 링크",
                        "search_query": "의심 링크를 누른 경우 대응 방법",
                        "evidence": evidence,
                    }
                ]
            }
        )

        self.assertEqual(result.guide_search_queries[0].evidence, evidence)

    def test_rejects_more_than_five_guide_search_queries(self) -> None:
        with self.assertRaises(ValidationError):
            GuideSearchQueryExtractionResult.model_validate(
                {
                    "guide_search_queries": [
                        {
                            "title": f"요구 {index}",
                            "search_query": f"검색 질의 {index}",
                            "evidence": "고객 원문",
                        }
                        for index in range(6)
                    ]
                }
            )

    def test_rejects_blank_or_oversized_guide_search_query_fields(self) -> None:
        invalid_items = [
            {"title": "", "search_query": "질의", "evidence": "원문"},
            {"title": "제목", "search_query": "", "evidence": "원문"},
            {"title": "제목", "search_query": "질의", "evidence": ""},
            {"title": "가" * 121, "search_query": "질의", "evidence": "원문"},
            {"title": "제목", "search_query": "가" * 501, "evidence": "원문"},
        ]
        for item in invalid_items:
            with self.subTest(item=item):
                with self.assertRaises(ValidationError):
                    GuideSearchQueryExtractionResult.model_validate(
                        {"guide_search_queries": [item]}
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
        for verdict in ("NON_ANSWER", "REFUSAL", "UNKNOWN"):
            with self.subTest(verdict=verdict), self.assertRaises(ValidationError):
                AnswerEvaluationResult.model_validate({"verdict": verdict})

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
                "transaction_id": 1,
                "top_fraud_types": [VOICE_PHISHING, MESSENGER_PHISHING],
            }
        )

        self.assertEqual(
            request.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

    def test_accepts_omitted_top_fraud_types(self) -> None:
        """룰 채점 실패 거래는 필드를 생략하고 일반 질문 폴백을 쓴다."""

        request = CreateChatRequest.model_validate({"transaction_id": 1})

        self.assertIsNone(request.top_fraud_types)

    def test_rejects_fraud_type_outside_whitelist(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": 1,
                    "top_fraud_types": [VOICE_PHISHING, "UNKNOWN_TYPE"],
                }
            )

    def test_rejects_wrong_item_count(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": 1,
                    "top_fraud_types": [VOICE_PHISHING],
                }
            )

        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": 1,
                    "top_fraud_types": [
                        VOICE_PHISHING,
                        MESSENGER_PHISHING,
                        VOICE_PHISHING,
                    ],
                }
            )

    def test_rejects_non_positive_transaction_id(self) -> None:
        """거래 id는 DB가 발급하는 양수 BIGINT다."""

        for transaction_id in (0, -1, "tx-1"):
            with self.subTest(transaction_id=transaction_id):
                with self.assertRaises(ValidationError):
                    CreateChatRequest.model_validate(
                        {"transaction_id": transaction_id}
                    )

    def test_rejects_duplicate_fraud_types(self) -> None:
        with self.assertRaises(ValidationError):
            CreateChatRequest.model_validate(
                {
                    "transaction_id": 1,
                    "top_fraud_types": [VOICE_PHISHING, VOICE_PHISHING],
                }
            )


if __name__ == "__main__":
    unittest.main()
