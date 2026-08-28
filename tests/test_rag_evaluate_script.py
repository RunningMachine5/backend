"""평가 스크립트의 진행 상황 출력 검증.

100건 평가는 십수 분이 걸리므로, 진행 출력에서 예외가 나면 그 긴 실행이 통째로
날아간다. 기권·오류·근거 없음 같은 변칙 입력에서도 출력이 죽지 않는지 확인한다.
LLM·DB 를 부르지 않는다.
"""

import io
import unittest
from contextlib import redirect_stderr

from app.scripts.evaluate_rag_ragas import (
    _parse_args,
    _format_duration,
    _print_summary,
    _ProgressPrinter,
    _RerankRateLimiter,
)
from app.services.rag.golden_dataset import GoldenCase, GoldenContext
from app.services.rag.ragas_evaluation import (
    RunResult,
    build_report,
    build_usage_section,
)


def _case(case_id, category, contexts=()):
    abstain = category == "unanswerable"
    return GoldenCase(
        id=case_id,
        category=category,
        difficulty="EASY" if category == "easy" else "HARD",
        user_input="질문",
        expected_behavior="ABSTAIN" if abstain else "ANSWER",
        reference="모범 답변",
        reference_contexts=tuple(contexts),
        required_claims=("주장",),
        forbidden_claims=("금지",),
        tags=(),
        review_status="DRAFT",
    )


def _context(document_id, page, role="guide"):
    return GoldenContext(
        document_id=document_id,
        filename=f"{document_id}.pdf",
        page=page,
        context_role=role,
        evidence="근거",
    )


class FormatDurationTest(unittest.TestCase):
    def test_1분_미만은_초만_쓴다(self):
        self.assertEqual(_format_duration(42.7), "42초")

    def test_1분_이상은_분과_초를_함께_쓴다(self):
        self.assertEqual(_format_duration(125), "2분 05초")

    def test_0초도_처리한다(self):
        self.assertEqual(_format_duration(0), "0초")


class RerankRateLimitOptionTest(unittest.TestCase):
    def test_옵션을_생략하면_호출_제한을_사용하지_않는다(self):
        self.assertIsNone(_parse_args([]).rerank_rpm)

    def test_분당_9회를_옵션으로_받는다(self):
        self.assertEqual(_parse_args(["--rerank-rpm", "9"]).rerank_rpm, 9)

    def test_0이하_RPM은_거부한다(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _parse_args(["--rerank-rpm", "0"])

    def test_실제_요청_시작을_분당_지정_횟수로_간격_제한한다(self):
        now = [100.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = _RerankRateLimiter(
            9,
            clock=lambda: now[0],
            sleep=sleep,
        )

        limiter()
        limiter()
        now[0] += 2
        limiter()

        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(sleeps[0], 60 / 9)
        self.assertAlmostEqual(sleeps[1], 60 / 9 - 2)


class ProgressPrinterTest(unittest.TestCase):
    def _emit(self, result, index=1, total=10):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            _ProgressPrinter()(index, total, result)
        return buffer.getvalue()

    def test_정상_사례를_출력한다(self):
        result = RunResult(
            case=_case("easy-001", "easy", [_context("F01", 5)]),
            response="안내",
            retrieved_contexts=("본문",),
            retrieved_locators=(("F01.pdf", 5),),
            search_queries=("질의",),
            elapsed_seconds=3.2,
            grounded_query_count=1,
        )
        line = self._emit(result)
        self.assertIn("easy-001", line)
        self.assertIn("청크  1", line)
        self.assertIn("3.2s", line)

    def test_기권_사례에_기권_표시가_붙는다(self):
        result = RunResult(
            case=_case("unans-001", "unanswerable"),
            response="",
            retrieved_contexts=(),
            retrieved_locators=(),
            search_queries=(),
        )
        line = self._emit(result)
        self.assertIn("기권", line)
        self.assertIn("청크  0", line)

    def test_오류_사례에도_출력이_터지지_않는다(self):
        result = RunResult(
            case=_case("easy-002", "easy", [_context("F01", 5)]),
            response="",
            retrieved_contexts=(),
            retrieved_locators=(),
            search_queries=(),
            error="analyze:EVALUATOR_FAILED",
        )
        line = self._emit(result)
        self.assertIn("오류", line)
        self.assertIn("EVALUATOR_FAILED", line)

    def test_첫_사례에서도_남은_시간이_계산된다(self):
        # index=1 일 때 0으로 나누지 않아야 한다.
        result = RunResult(
            case=_case("easy-001", "easy", [_context("F01", 5)]),
            response="안내",
            retrieved_contexts=("본문",),
            retrieved_locators=(("F01.pdf", 5),),
            search_queries=(),
        )
        self.assertIn("남은 시간", self._emit(result, index=1, total=100))


class PrintSummaryTest(unittest.TestCase):
    def test_점수가_없어도_요약이_출력된다(self):
        results = [
            RunResult(
                case=_case("unans-001", "unanswerable"),
                response="",
                retrieved_contexts=(),
                retrieved_locators=(),
                search_queries=(),
            )
        ]
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            _print_summary(build_report(results, {}))
        out = buffer.getvalue()
        self.assertIn("context_precision", out)
        self.assertIn("unanswerable", out)

    def test_토큰_비용이_요약에_함께_나온다(self):
        usage = build_usage_section(
            {"gpt-5.6-luna": {"input_tokens": 1_000_000, "output_tokens": 0}},
            {"gpt-5.6-terra": {"input_tokens": 1_000_000, "output_tokens": 0}},
            case_count=1,
        )
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            _print_summary(build_report([], {}, usage=usage))
        out = buffer.getvalue()
        self.assertIn("파이프라인", out)
        self.assertIn("RAGAS 심판", out)
        self.assertIn("$0.2000", out)
        self.assertIn("$2.2000", out)

    def test_usage_가_없는_리포트도_요약이_출력된다(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            _print_summary(build_report([], {}))
        self.assertIn("context_precision", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
