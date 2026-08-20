import unittest

from app.dto.chatbot import AnswerQualityVerdict
from app.services.chatbot.answer_analyzer import AnswerAnalyzer
from app.services.chatbot.prompts import render_answer_analysis_prompt


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


class TestAnswerAnalyzer(unittest.TestCase):
    def test_sufficient_returns_normalized_deduplicated_queries_in_one_call(self) -> None:
        question = "이번 거래를 어떻게 하셨나요?"
        answer = "문자 링크를 눌렀어요"
        llm = FakeStructuredLLM(
            [
                {
                    "verdict": "SUFFICIENT",
                    "guide_search_queries": [
                        {
                            "title": "  의심 링크  ",
                            "search_query": "  문자 링크를   눌렀을 때 대응 방법  ",
                            "evidence": answer,
                        },
                        {
                            "title": "중복",
                            "search_query": "문자 링크를 눌렀을 때 대응 방법",
                            "evidence": answer,
                        },
                    ],
                }
            ]
        )

        outcome = AnswerAnalyzer(
            structured_llm=llm,
            max_attempts=2,
        ).analyze(question_text=question, customer_answer=answer)

        self.assertEqual(outcome.quality_verdict, AnswerQualityVerdict.SUFFICIENT)
        self.assertEqual(len(outcome.guide_search_queries), 1)
        self.assertEqual(outcome.guide_search_queries[0].title, "의심 링크")
        self.assertEqual(
            outcome.guide_search_queries[0].search_query,
            "문자 링크를 눌렀을 때 대응 방법",
        )
        self.assertEqual(
            llm.calls,
            [
                render_answer_analysis_prompt(
                    question_text=question,
                    customer_answer=answer,
                )
            ],
        )

    def test_non_sufficient_verdicts_discard_queries(self) -> None:
        for verdict in (
            AnswerQualityVerdict.TOO_VAGUE,
            AnswerQualityVerdict.WANT_END,
        ):
            with self.subTest(verdict=verdict):
                llm = FakeStructuredLLM(
                    [
                        {
                            "verdict": verdict.value,
                            "guide_search_queries": [
                                {
                                    "title": "폐기 대상",
                                    "search_query": "사용하지 않을 검색 질의",
                                    "evidence": "답변",
                                }
                            ],
                        }
                    ]
                )

                outcome = AnswerAnalyzer(structured_llm=llm).analyze(
                    question_text="질문",
                    customer_answer="답변",
                )

                self.assertEqual(outcome.quality_verdict, verdict)
                self.assertEqual(outcome.guide_search_queries, ())
                self.assertEqual(len(llm.calls), 1)

    def test_retry_exhaustion_returns_evaluator_failed_without_fallback(self) -> None:
        llm = FakeStructuredLLM(
            [TimeoutError("timeout"), ConnectionError("disconnected")]
        )

        with self.assertLogs(
            "app.services.chatbot.answer_analyzer",
            level="WARNING",
        ):
            outcome = AnswerAnalyzer(
                structured_llm=llm,
                max_attempts=2,
            ).analyze(question_text="질문", customer_answer="답변")

        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(outcome.quality_verdict)
        self.assertEqual(outcome.guide_search_queries, ())
        self.assertEqual(outcome.verdict_skip_reason, "EVALUATOR_FAILED")

    def test_prompt_requires_empty_queries_for_non_sufficient_verdict(self) -> None:
        prompt = render_answer_analysis_prompt(
            question_text="질문",
            customer_answer="그만할게요",
        )

        self.assertIn("TOO_VAGUE 또는 WANT_END", prompt)
        self.assertIn("guide_search_queries는 반드시 빈 배열", prompt)
        self.assertIn("가이드 검색 질의 규칙", prompt)


if __name__ == "__main__":
    unittest.main()
