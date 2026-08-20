import unittest

from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
)
from app.services.chatbot.extractors import (
    FraudCircumstanceExtractor,
)
from app.services.chatbot.prompts import (
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
