import unittest

from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
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

    def test_keeps_continuous_evidence_spanning_multiple_sentences(self) -> None:
        answer = (
            "검찰청이라고 전화가 왔어요. "
            "제 통장이 범죄 자금 세탁에 쓰였다고 했습니다."
        )
        circumstance_llm = FakeStructuredLLM(
            [
                {
                    "fraud_circumstances": [
                        {
                            "type": CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
                            "evidence": answer,
                        }
                    ]
                }
            ]
        )
        extractor = FraudCircumstanceExtractor(
            structured_llm=circumstance_llm,
        )

        result = extractor.extract(user_answers=answer)

        self.assertEqual(len(result.fraud_circumstances), 1)
        self.assertEqual(
            result.fraud_circumstances[0].type,
            CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
        )

    def test_prompt_combines_context_without_relaxing_completion_rules(
        self,
    ) -> None:
        prompt = render_fraud_circumstance_extraction_prompt(
            user_answers="테스트 답변"
        )

        self.assertIn(
            "여러 문장이나 절에 나뉜 정보를 함께 판단합니다",
            prompt,
        )
        self.assertIn(
            "정의가 실제 이체·입력·전달·게시 등 행동 완료를 요구할 때만",
            prompt,
        )
        self.assertIn("정황들은 서로 배타적이지 않습니다", prompt)
        self.assertIn(
            "gift_card_pin_requested_by_impersonated_contact을 추출하면",
            prompt,
        )
        self.assertIn("여러 문장을 포함해도 됩니다", prompt)
        self.assertNotIn(
            "하나의 연속된 원문만으로 정황이 입증되지 않으면",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
