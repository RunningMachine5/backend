import unittest

from app.dto.chatbot import AnswerQualityVerdict
from app.services.chatbot.answer_evaluator import AnswerEvaluator
from app.services.chatbot.prompts import render_quality_check_prompt


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


class TestAnswerEvaluator(unittest.TestCase):
    def test_returns_all_five_structured_verdicts(self) -> None:
        question = "이 거래를 직접 실행하셨나요?"
        answer = "제가 직접 송금했어요."

        for verdict in AnswerQualityVerdict:
            with self.subTest(verdict=verdict):
                llm = FakeStructuredLLM([{"verdict": verdict.value}])
                outcome = AnswerEvaluator(
                    structured_llm=llm,
                    max_attempts=2,
                ).evaluate(
                    question_text=question,
                    customer_answer=answer,
                )

                self.assertEqual(outcome.routing_verdict, verdict)
                self.assertEqual(outcome.quality_verdict, verdict)
                self.assertIsNone(outcome.verdict_skip_reason)
                self.assertEqual(
                    llm.calls,
                    [
                        render_quality_check_prompt(
                            question_text=question,
                            customer_answer=answer,
                        )
                    ],
                )

    def test_retry_exhaustion_returns_refusal_routing_fallback(self) -> None:
        llm = FakeStructuredLLM(
            [TimeoutError("timeout"), ConnectionError("disconnected")]
        )

        with self.assertLogs(
            "app.services.chatbot.answer_evaluator",
            level="WARNING",
        ):
            outcome = AnswerEvaluator(
                structured_llm=llm,
                max_attempts=2,
            ).evaluate(
                question_text="질문",
                customer_answer="답변",
            )

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(
            outcome.routing_verdict,
            AnswerQualityVerdict.REFUSAL,
        )
        self.assertIsNone(outcome.quality_verdict)
        self.assertEqual(outcome.verdict_skip_reason, "EVALUATOR_FAILED")


if __name__ == "__main__":
    unittest.main()
