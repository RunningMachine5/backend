import unittest

from app.domain.fraud_circumstance_codes import (
    ACCOUNT_REAUTHENTICATION_PHISHING,
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE,
)
from app.services.chatbot.extractors import (
    ChatbotExtractionError,
    FraudCircumstanceExtractor,
    GuideSearchQueryExtractor,
)
from app.services.chatbot.prompts import (
    render_fraud_circumstance_extraction_prompt,
    render_guide_search_query_extraction_prompt,
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


class TestGuideSearchQueryExtractor(unittest.TestCase):
    def test_keeps_order_and_only_evidence_from_original_answer(self) -> None:
        answer = (
            "오늘 ATM기에서 십만원을 입금했고 모르는 사람한테 전화가 와서 "
            "받았어 그 사람에게 전화번호를 전송해줬어"
        )
        llm = FakeStructuredLLM(
            [
                {
                    "guide_search_queries": [
                        {
                            "title": "모르는 사람의 전화 수신",
                            "search_query": "모르는 사람의 전화를 받은 경우 보안 대응 방법",
                            "evidence": "모르는 사람한테 전화가 와서 받았어",
                        },
                        {
                            "title": "전화번호 제공",
                            "search_query": "모르는 사람에게 전화번호를 제공한 경우 대응 방법",
                            "evidence": "그 사람에게 전화번호를 전송해줬어",
                        },
                        {
                            "title": "꾸며낸 앱 설치",
                            "search_query": "의심 앱을 설치한 경우 대응 방법",
                            "evidence": "앱을 설치했어",
                        },
                    ]
                }
            ]
        )
        extractor = GuideSearchQueryExtractor(structured_llm=llm)

        with self.assertLogs(
            "app.services.chatbot.extractors",
            level="WARNING",
        ):
            result = extractor.extract(user_answers=answer)

        self.assertEqual(
            [query.title for query in result.guide_search_queries],
            ["모르는 사람의 전화 수신", "전화번호 제공"],
        )
        self.assertEqual(
            llm.calls,
            [render_guide_search_query_extraction_prompt(user_answers=answer)],
        )
        self.assertNotIn("ATM", " ".join(
            query.search_query for query in result.guide_search_queries
        ))

    def test_normalizes_and_deduplicates_search_queries(self) -> None:
        answer = "모르는 사람에게 전화번호를 보냈어요"
        llm = FakeStructuredLLM(
            [
                {
                    "guide_search_queries": [
                        {
                            "title": "전화번호 제공",
                            "search_query": "  전화번호를   제공한 경우 대응 방법  ",
                            "evidence": "전화번호를 보냈어요",
                        },
                        {
                            "title": "중복 요구",
                            "search_query": "전화번호를 제공한 경우 대응 방법",
                            "evidence": "전화번호를 보냈어요",
                        },
                    ]
                }
            ]
        )

        result = GuideSearchQueryExtractor(structured_llm=llm).extract(
            user_answers=answer
        )

        self.assertEqual(len(result.guide_search_queries), 1)
        self.assertEqual(
            result.guide_search_queries[0].search_query,
            "전화번호를 제공한 경우 대응 방법",
        )

    def test_empty_result_is_allowed(self) -> None:
        extractor = GuideSearchQueryExtractor(
            structured_llm=FakeStructuredLLM([{"guide_search_queries": []}])
        )

        result = extractor.extract(user_answers="오늘 날씨가 좋네요")

        self.assertEqual(result.guide_search_queries, [])

    def test_extraction_failure_retries_then_raises_service_error(self) -> None:
        llm = FakeStructuredLLM(
            [TimeoutError("timeout"), ConnectionError("disconnected")]
        )
        extractor = GuideSearchQueryExtractor(
            structured_llm=llm,
            max_attempts=2,
        )

        with self.assertLogs(
            "app.services.chatbot.extractors",
            level="WARNING",
        ):
            with self.assertRaises(ChatbotExtractionError):
                extractor.extract(user_answers="답변")

        self.assertEqual(len(llm.calls), 2)


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
