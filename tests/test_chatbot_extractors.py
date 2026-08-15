import unittest

from app.domain.customer_action_codes import (
    PHISHING_LINK_OPENED,
    SUSPICIOUS_APP_INSTALLED,
)
from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
)
from app.services.chatbot.extractors import (
    ChatbotExtractionError,
    CustomerActionExtractor,
    FraudCircumstanceExtractor,
)
from app.services.chatbot.prompts import (
    render_customer_action_extraction_prompt,
    render_fraud_circumstance_extraction_prompt,
)


class FakeStructuredLLM:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[str] = []

    def invoke(self, prompt: str) -> object:
        self.calls.append(prompt)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TestCustomerActionExtractor(unittest.TestCase):
    def test_customer_action_keeps_only_evidence_from_original_answer(self) -> None:
        answer = "문자로 받은 링크를 눌렀어요."
        action_llm = FakeStructuredLLM(
            [
                {
                    "customer_actions": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "evidence": "링크를 눌렀어요",
                        },
                        {
                            "type": SUSPICIOUS_APP_INSTALLED,
                            "evidence": "앱을 설치했어요",
                        },
                    ]
                }
            ]
        )
        extractor = CustomerActionExtractor(
            structured_llm=action_llm,
        )

        with self.assertLogs(
            "app.services.chatbot.extractors",
            level="WARNING",
        ):
            result = extractor.extract(user_answers=answer)

        self.assertEqual(len(result.customer_actions), 1)
        self.assertEqual(result.customer_actions[0].type, PHISHING_LINK_OPENED)
        self.assertEqual(
            action_llm.calls,
            [render_customer_action_extraction_prompt(user_answers=answer)],
        )

    def test_extraction_failure_retries_then_raises_service_error(self) -> None:
        action_llm = FakeStructuredLLM(
            [TimeoutError("timeout"), ConnectionError("disconnected")]
        )
        extractor = CustomerActionExtractor(
            structured_llm=action_llm,
            max_attempts=2,
        )

        with self.assertLogs(
            "app.services.chatbot.extractors",
            level="WARNING",
        ):
            with self.assertRaises(ChatbotExtractionError):
                extractor.extract(user_answers="답변")

        self.assertEqual(len(action_llm.calls), 2)


class TestFraudCircumstanceExtractor(unittest.TestCase):
    def test_keeps_only_evidence_from_original_answer(self) -> None:
        answer = "휴대폰이 고장 났다며 다른 번호로 연락했어요."
        circumstance_llm = FakeStructuredLLM(
            [
                {
                    "fraud_circumstances": [
                        {
                            "type": BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
                            "evidence": "휴대폰이 고장 났다며",
                        },
                        {
                            "type": ACCOUNT_REAUTHENTICATION_PHISHING,
                            "evidence": "계정 재인증을 요구했어요",
                        },
                    ]
                }
            ]
        )
        extractor = FraudCircumstanceExtractor(
            structured_llm=circumstance_llm,
        )

        with self.assertLogs(
            "app.services.chatbot.extractors",
            level="WARNING",
        ):
            result = extractor.extract(user_answers=answer)

        self.assertEqual(len(result.fraud_circumstances), 1)
        self.assertEqual(
            result.fraud_circumstances[0].type,
            BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
        )
        self.assertEqual(
            circumstance_llm.calls,
            [render_fraud_circumstance_extraction_prompt(user_answers=answer)],
        )


if __name__ == "__main__":
    unittest.main()
