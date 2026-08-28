"""기존 RAG 리포트의 Answer Relevancy 후채점 로직을 API 호출 없이 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.rag.golden_dataset import GoldenCase
from app.scripts.evaluate_answer_relevancy import (
    OUTPUT_SUFFIX,
    _SAME_LANGUAGE_INSTRUCTION,
    add_answer_relevancy,
    build_samples,
    discover_reports,
    evaluate_report,
    is_rag_answer_report,
    score_samples,
)


def _golden(case_id, category, difficulty="EASY"):
    return GoldenCase(
        id=case_id,
        category=category,
        difficulty=difficulty,
        user_input=f"{case_id} 원래 질문",
        expected_behavior="ABSTAIN" if category == "unanswerable" else "ANSWER",
        reference=(
            UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
            if category == "unanswerable"
            else "모범 답변"
        ),
        reference_contexts=(),
        required_claims=(),
        forbidden_claims=(),
        tags=(),
        review_status="DRAFT",
    )


def _report():
    return {
        "case_count": 3,
        "usage": {"judge": {"total_cost_usd": 1.0}},
        "overall": {"count": 3, "faithfulness": 0.5},
        "by_category": {
            "easy": {"count": 1},
            "hard": {"count": 1},
            "unanswerable": {"count": 1},
        },
        "by_difficulty": {
            "EASY": {"count": 2},
            "HARD": {"count": 1},
        },
        "cases": [
            {
                "id": "easy-001",
                "category": "easy",
                "response": "질문에 맞는 답변",
                "scores": {"faithfulness": 0.8},
            },
            {
                "id": "hard-001",
                "category": "hard",
                "response": "",
                "scores": {"faithfulness": 0.2},
            },
            {
                "id": "unans-001",
                "category": "unanswerable",
                "response": "안내할 자료가 없습니다",
                "scores": {},
            },
        ],
    }


class DiscoverReportsTest(unittest.TestCase):
    def test_폴더를_재귀_탐색하고_기본_출력물은_제외한다(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            source = nested / "report.json"
            generated = nested / f"report{OUTPUT_SUFFIX}.json"
            source.write_text("{}", encoding="utf-8")
            generated.write_text("{}", encoding="utf-8")

            self.assertEqual(discover_reports([root]), [source.resolve()])
            # 직접 지정한 출력 파일은 사용자의 명시적 입력이므로 허용한다.
            self.assertEqual(discover_reports([generated]), [generated.resolve()])


class BuildSamplesTest(unittest.TestCase):
    def setUp(self):
        self.golden = {
            "easy-001": _golden("easy-001", "easy"),
            "hard-001": _golden("hard-001", "hard", "HARD"),
            "unans-001": _golden("unans-001", "unanswerable"),
        }

    def test_답변_가능_사례만_만들고_빈_응답은_별도_0점_대상이다(self):
        samples, empty_ids = build_samples(_report(), self.golden)

        self.assertEqual(
            samples,
            [
                {
                    "case_id": "easy-001",
                    "user_input": "easy-001 원래 질문",
                    "response": "질문에 맞는 답변",
                }
            ],
        )
        self.assertEqual(empty_ids, ["hard-001"])

    def test_리포트와_골든셋_카테고리가_다르면_실패한다(self):
        report = _report()
        report["cases"][0]["category"] = "hard"
        with self.assertRaisesRegex(ValueError, "카테고리가 골든셋과 다릅니다"):
            build_samples(report, self.golden)

    def test_검색_전용_리포트는_답변_리포트가_아니다(self):
        self.assertFalse(
            is_rag_answer_report({"cases": [{"id": "easy-001", "query": "질문"}]})
        )


class AddAnswerRelevancyTest(unittest.TestCase):
    def setUp(self):
        self.golden = {
            "easy-001": _golden("easy-001", "easy"),
            "hard-001": _golden("hard-001", "hard", "HARD"),
            "unans-001": _golden("unans-001", "unanswerable"),
        }

    def test_사례와_전체_슬라이스에_점수를_병합한다(self):
        original = _report()
        with tempfile.TemporaryDirectory() as directory:
            golden_path = Path(directory) / "golden.jsonl"
            golden_path.write_text('{"id":"dummy"}\n', encoding="utf-8")
            updated = add_answer_relevancy(
                original,
                {"easy-001": 0.8, "hard-001": 0.0},
                self.golden,
                judge_usage={"total_cost_usd": 0.01},
                embedding_model="text-embedding-3-small",
                strictness=3,
                golden_set_path=golden_path,
            )

        self.assertEqual(updated["overall"]["answer_relevancy"], 0.4)
        self.assertEqual(updated["by_category"]["easy"]["answer_relevancy"], 0.8)
        self.assertEqual(updated["by_category"]["hard"]["answer_relevancy"], 0.0)
        self.assertIsNone(
            updated["by_category"]["unanswerable"]["answer_relevancy"]
        )
        self.assertEqual(updated["by_difficulty"]["EASY"]["answer_relevancy"], 0.8)
        self.assertEqual(updated["by_difficulty"]["HARD"]["answer_relevancy"], 0.0)
        self.assertEqual(
            updated["cases"][0]["scores"]["answer_relevancy"], 0.8
        )
        self.assertNotIn("answer_relevancy", updated["cases"][2]["scores"])
        metadata = updated["answer_relevancy_evaluation"]
        self.assertEqual(metadata["scored_count"], 2)
        self.assertEqual(metadata["llm_evaluated_count"], 1)
        self.assertEqual(metadata["empty_response_count"], 1)
        self.assertEqual(metadata["excluded_categories"], ["unanswerable"])
        # 기존 실행 비용과 입력 객체는 바꾸지 않는다.
        self.assertEqual(updated["usage"], original["usage"])
        self.assertNotIn("answer_relevancy", original["overall"])


class ScoreSamplesTest(unittest.TestCase):
    def test_RAGAS_결과를_id별_점수로_변환하고_한국어_유지_지시를_넣는다(self):
        fake_result = mock.Mock()
        fake_frame = mock.Mock()
        fake_frame.to_dict.return_value = [{"answer_relevancy": 0.75}]
        fake_result.to_pandas.return_value = fake_frame

        with (
            mock.patch("ragas.evaluate", return_value=fake_result) as evaluate,
            mock.patch(
                "app.scripts.evaluate_answer_relevancy.build_judge_llm",
                return_value=mock.sentinel.llm,
            ),
            mock.patch(
                "app.scripts.evaluate_answer_relevancy.OpenAIEmbeddings",
                return_value=mock.sentinel.embeddings,
            ),
        ):
            scores, usage = score_samples(
                [
                    {
                        "case_id": "easy-001",
                        "user_input": "원래 질문",
                        "response": "생성 답변",
                    }
                ]
            )

        self.assertEqual(scores, {"easy-001": 0.75})
        self.assertEqual(usage["total_cost_usd"], 0)
        metric = evaluate.call_args.kwargs["metrics"][0]
        self.assertIn(
            _SAME_LANGUAGE_INSTRUCTION, metric.question_generation.instruction
        )
        self.assertEqual(
            evaluate.call_args.kwargs["embeddings"], mock.sentinel.embeddings
        )


class EvaluateReportTest(unittest.TestCase):
    def test_원본을_보존하고_별도_결과_파일을_만든다(self):
        golden = {
            "easy-001": _golden("easy-001", "easy"),
            "hard-001": _golden("hard-001", "hard", "HARD"),
            "unans-001": _golden("unans-001", "unanswerable"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.json"
            golden_path = root / "golden.jsonl"
            source.write_text(json.dumps(_report()), encoding="utf-8")
            golden_path.write_text('{"id":"dummy"}\n', encoding="utf-8")

            with mock.patch(
                "app.scripts.evaluate_answer_relevancy.score_samples",
                return_value=(
                    {"easy-001": 0.8},
                    {"total_cost_usd": 0.01},
                ),
            ):
                status, destination, average = evaluate_report(
                    source,
                    golden_by_id=golden,
                    golden_set_path=golden_path,
                    embedding_model="text-embedding-3-small",
                    strictness=3,
                    in_place=False,
                    force=False,
                )

            original = json.loads(source.read_text(encoding="utf-8"))
            written = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(status, "완료")
        self.assertEqual(destination.name, f"report{OUTPUT_SUFFIX}.json")
        # hard-001은 빈 답변이라 API를 부르지 않고 0점이 병합된다.
        self.assertEqual(average, 0.4)
        self.assertNotIn("answer_relevancy", original["overall"])
        self.assertEqual(written["overall"]["answer_relevancy"], 0.4)

    def test_검색_전용_리포트는_API_호출_없이_건너뛴다(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "retrieval.json"
            source.write_text(
                json.dumps({"cases": [{"id": "easy-001", "query": "질문"}]}),
                encoding="utf-8",
            )
            with mock.patch(
                "app.scripts.evaluate_answer_relevancy.score_samples"
            ) as score:
                status, destination, average = evaluate_report(
                    source,
                    golden_by_id={"easy-001": _golden("easy-001", "easy")},
                    golden_set_path=source,
                    embedding_model="text-embedding-3-small",
                    strictness=3,
                    in_place=False,
                    force=False,
                )

        self.assertEqual(status, "답변 없는 검색 전용 리포트")
        self.assertIsNone(destination)
        self.assertIsNone(average)
        score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
