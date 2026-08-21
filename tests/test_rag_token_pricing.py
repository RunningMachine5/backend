"""토큰 사용량 → 비용 추정과 리포트용 usage 집계."""

import unittest

from app.services.rag.token_pricing import build_usage_report, resolve_pricing


def _usage(input_tokens, output_tokens, cache_read=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {"cache_read": cache_read},
    }


class BuildUsageReportTest(unittest.TestCase):
    def test_모델별_내역과_합계를_담는다(self):
        report = build_usage_report(
            {
                "gpt-5.6-luna": _usage(1_000_000, 1_000_000),
                "text-embedding-3-small": _usage(1_000_000, 0),
            }
        )

        self.assertEqual(
            [row["model"] for row in report["by_model"]],
            ["gpt-5.6-luna", "text-embedding-3-small"],
        )
        self.assertEqual(report["input_tokens"], 2_000_000)
        self.assertEqual(report["output_tokens"], 1_000_000)
        # luna 입력 0.20 + 출력 1.20 + 임베딩 0.02
        self.assertAlmostEqual(report["total_cost_usd"], 1.42)
        self.assertTrue(report["total_cost_known"])

    def test_캐시_입력은_캐시_단가로_계산한다(self):
        report = build_usage_report(
            {"gpt-5.6-luna": _usage(1_000_000, 0, cache_read=1_000_000)}
        )

        self.assertEqual(report["cached_input_tokens"], 1_000_000)
        self.assertAlmostEqual(report["total_cost_usd"], 0.02)

    def test_단가를_모르는_모델은_합계에서_빠지고_표시가_남는다(self):
        # 합계를 그대로 믿으면 과소 추정이 된다. 그 사실이 리포트에 남아야 한다.
        self.assertIsNone(resolve_pricing("some-unlisted-model"))
        report = build_usage_report(
            {
                "gpt-5.6-luna": _usage(1_000_000, 0),
                "some-unlisted-model": _usage(1_000_000, 1_000_000),
            }
        )

        self.assertFalse(report["total_cost_known"])
        self.assertAlmostEqual(report["total_cost_usd"], 0.20)
        unlisted = next(
            row for row in report["by_model"] if row["model"] == "some-unlisted-model"
        )
        self.assertIsNone(unlisted["cost_usd"])

    def test_사례_수를_주면_사례당_비용도_담는다(self):
        report = build_usage_report(
            {"gpt-5.6-luna": _usage(1_000_000, 0)}, case_count=100
        )
        self.assertAlmostEqual(report["cost_usd_per_case"], 0.002)

    def test_호출_기록이_없어도_비용은_0으로_집계된다(self):
        report = build_usage_report({}, case_count=10)
        self.assertEqual(report["by_model"], [])
        self.assertEqual(report["total_cost_usd"], 0.0)
        self.assertTrue(report["total_cost_known"])


if __name__ == "__main__":
    unittest.main()
