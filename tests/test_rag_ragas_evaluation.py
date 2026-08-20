"""RAGAS 하네스의 실행·집계 로직을 LLM/DB 없이 검증한다.

ragas 자체(지표 계산)는 eval 전용 의존성이라 여기서 부르지 않는다. 검증 대상은
파이프라인 실행 결과를 모으는 부분과 슬라이스 리포트를 만드는 부분이다.
"""

import unittest

from app.dto.chatbot import (
    ExtractedGuideSearchQuery,
    GuideSearchQueryExtractionResult,
    RetrievedChatbotGuideChunkDTO,
)
from app.services.chatbot.guide_responder import GuideResponse
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.rag.golden_dataset import GoldenCase, GoldenContext
from app.services.rag.ragas_evaluation import (
    RunResult,
    _pick_scores,
    build_report,
    run_cases,
)


def _context(document_id, page, role="guide"):
    return GoldenContext(
        document_id=document_id,
        filename=f"{document_id}.pdf",
        page=page,
        context_role=role,
        evidence=f"{document_id} p{page} 근거",
    )


def _case(case_id, category, contexts=(), difficulty=None):
    abstain = category == "unanswerable"
    return GoldenCase(
        id=case_id,
        category=category,
        difficulty=difficulty or ("EASY" if category == "easy" else "HARD"),
        user_input=f"{case_id} 질문",
        expected_behavior="ABSTAIN" if abstain else "ANSWER",
        reference=UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE if abstain else "모범 답변",
        reference_contexts=tuple(contexts),
        required_claims=("주장",),
        forbidden_claims=("금지",),
        tags=("태그",),
        review_status="DRAFT",
    )


class _StubExtractor:
    """질의 분해 LLM 대역. queries_by_case 에 없는 사례는 0건을 낸다."""

    def __init__(self, queries_by_case):
        self.queries_by_case = queries_by_case

    def extract(self, *, user_answers):
        case_id = user_answers.split()[0]
        queries = self.queries_by_case.get(case_id, [])
        return GuideSearchQueryExtractionResult(
            guide_search_queries=[
                ExtractedGuideSearchQuery(
                    title="소제목", search_query=q, evidence="근거"
                )
                for q in queries
            ]
        )


class _StubResponder:
    """검색만 실제처럼 부르고 생성은 고정 문구로 대신한다."""

    def __init__(self, retriever, message, grounded=None):
        self.retriever = retriever
        self.message = message
        self.grounded = grounded

    def respond(self, *, guide_search_queries, session):
        queries = list(guide_search_queries)
        for query in queries:
            self.retriever(query.search_query, session, top_k=3)
        positions = (
            tuple(range(1, len(queries) + 1))
            if self.grounded is None
            else tuple(self.grounded)
        )
        return GuideResponse(
            message_text=self.message, grounded_query_positions=positions
        )


class RunCasesTest(unittest.TestCase):
    """실행 결과 수집: 검색 청크·출처·기권 판정."""

    def setUp(self):
        self.chunks = [
            RetrievedChatbotGuideChunkDTO(
                content="C02 2페이지 본문",
                source_title="C02.pdf",
                page=2,
                distance=0.1,
            )
        ]

    def _run(self, cases, queries, message, chunks=None, grounded=None):
        found = list(self.chunks if chunks is None else chunks)

        def fake_retriever(question, session, top_k=3):
            return found

        return run_cases(
            tuple(cases),
            session=None,
            extractor=_StubExtractor(queries),
            retriever=fake_retriever,
            responder_factory=lambda recorder, top_k: _StubResponder(
                recorder, message, grounded
            ),
        )

    def test_검색_청크와_출처가_기록된다(self):
        case = _case("easy-001", "easy", [_context("C02", 2)])
        results = self._run([case], {"easy-001": ["스미싱 신고"]}, "안내 본문")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].retrieved_contexts, ("C02 2페이지 본문",))
        self.assertEqual(results[0].retrieved_locators, (("C02.pdf", 2),))
        self.assertEqual(results[0].search_queries, ("스미싱 신고",))
        self.assertFalse(results[0].abstained)

    def test_질의_분해가_0건이면_응답이_비고_기권으로_본다(self):
        case = _case("unans-001", "unanswerable")
        results = self._run([case], {}, "안내 본문")
        self.assertEqual(results[0].response, "")
        self.assertEqual(results[0].retrieved_contexts, ())
        self.assertTrue(results[0].abstained)

    def test_모든_질의가_근거를_못_찾으면_기권이다(self):
        case = _case("unans-002", "unanswerable")
        results = self._run(
            [case],
            {"unans-002": ["없는 주제"]},
            f"■ 소제목\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}",
            chunks=[],
            grounded=(),
        )
        self.assertTrue(results[0].abstained)

    def test_일부_질의만_근거를_찾으면_기권이_아니다(self):
        # 질의 2개 중 1개만 근거를 찾으면 응답에 답변과 B.5 문구가 함께 들어간다.
        # 이를 기권으로 세면 절반은 답한 턴까지 기권으로 잡힌다.
        case = _case("easy-009", "easy", [_context("F04", 5)])
        results = self._run(
            [case],
            {"easy-009": ["질의1", "질의2"]},
            f"■ 답변 섹션\n안내 본문\n\n■ 다른 요구\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}",
            grounded=(1,),
        )
        self.assertFalse(results[0].abstained)
        self.assertEqual(results[0].grounded_query_count, 1)

    def test_근거는_찾았지만_안내가_비면_기권으로_본다(self):
        # 검색은 성공해 grounded_query_count 가 1 이지만, 생성 LLM 이 그 자리의
        # 안내를 내놓지 못해 GuideResponder 가 B.5 문구로 메운 경우다. 고객이 받은
        # 것은 기권 응답이므로 지표도 기권으로 세야 한다.
        case = _case("easy-001", "easy", [_context("C02", 2)])
        results = self._run(
            [case],
            {"easy-001": ["지급정지 방법"]},
            f"■ 소제목\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}",
            grounded=(1,),
        )
        self.assertEqual(results[0].grounded_query_count, 1)
        self.assertTrue(results[0].abstained)

    def test_경계_사례도_두_페이지를_정답_근거로_남긴다(self):
        # 파생 지표는 두지 않고, 검색된 출처와 정답 근거를 그대로 리포트에 남긴다.
        case = _case(
            "boundary-001",
            "page_boundary",
            [_context("C02", 1, "behavior"), _context("C02", 2, "guide")],
        )
        results = self._run([case], {"boundary-001": ["대응 방법"]}, "안내 본문")
        self.assertEqual(results[0].retrieved_locators, (("C02.pdf", 2),))
        self.assertEqual(
            sorted(case.locators()), [("C02.pdf", 1), ("C02.pdf", 2)]
        )


class BuildReportTest(unittest.TestCase):
    """슬라이스 집계."""

    def setUp(self):
        self.results = [
            RunResult(
                case=_case("easy-001", "easy", [_context("C02", 2)]),
                response="안내",
                retrieved_contexts=("C02 본문",),
                retrieved_locators=(("C02.pdf", 2),),
                search_queries=("질의",),
                grounded_query_count=1,
            ),
            RunResult(
                case=_case("hard-001", "hard", [_context("F05", 5)]),
                response="안내",
                retrieved_contexts=("F05 본문",),
                retrieved_locators=(("F05.pdf", 9),),
                search_queries=("질의",),
                grounded_query_count=1,
            ),
            RunResult(
                case=_case("unans-001", "unanswerable"),
                response="",
                retrieved_contexts=(),
                retrieved_locators=(),
                search_queries=(),
            ),
        ]
        self.scores = {
            "easy-001": {
                "context_precision": 1.0,
                "context_recall": 1.0,
                "faithfulness": 0.9,
                "factual_correctness": 0.8,
            },
            "hard-001": {
                "context_precision": 0.0,
                "context_recall": 0.0,
                "faithfulness": 0.5,
                "factual_correctness": 0.4,
            },
            "unans-001": {"faithfulness": 1.0, "factual_correctness": 1.0},
        }

    def test_네_지표_모두_무근거_사례를_평균에서_제외한다(self):
        # 무근거 사례는 채점할 응답 자체가 없다. 질의가 0건이면 빈 문자열이고,
        # 검색이 0건일 때만 B.5 문구라 정답 응답이 두 가지여서 비교가 불가능하다.
        overall = build_report(self.results, self.scores)["overall"]
        self.assertEqual(overall["context_precision"], 0.5)
        self.assertEqual(overall["context_recall"], 0.5)
        self.assertAlmostEqual(overall["faithfulness"], round((0.9 + 0.5) / 2, 4))
        self.assertAlmostEqual(
            overall["factual_correctness"], round((0.8 + 0.4) / 2, 4)
        )
        self.assertEqual(overall["scored_count"], 2)

    def test_기권율은_전체_대비_비율이다(self):
        overall = build_report(self.results, self.scores)["overall"]
        self.assertAlmostEqual(overall["abstain_rate"], round(1 / 3, 4))

    def test_카테고리와_난이도로_쪼갠다(self):
        report = build_report(self.results, self.scores)
        self.assertEqual(set(report["by_category"]), {"easy", "hard", "unanswerable"})
        self.assertEqual(set(report["by_difficulty"]), {"EASY", "HARD"})
        self.assertEqual(report["by_category"]["easy"]["context_precision"], 1.0)
        unanswerable = report["by_category"]["unanswerable"]
        self.assertEqual(unanswerable["abstain_rate"], 1.0)
        # 무근거는 abstain_rate 로만 본다. 나머지 지표는 값이 없다.
        for metric in ("context_precision", "context_recall",
                       "faithfulness", "factual_correctness"):
            self.assertIsNone(unanswerable[metric], metric)

    def test_파생_지표는_두지_않는다(self):
        overall = build_report(self.results, self.scores)["overall"]
        self.assertNotIn("page_hit_rate", overall)

    def test_리포트에_page_boundary_섹션이_없다(self):
        # 경계 진단도 파생 지표라 걷어냈다. 원인은 retrieved_locators 로 확인한다.
        self.assertNotIn("page_boundary", build_report(self.results, self.scores))

    def test_사례별_상세가_남는다(self):
        report = build_report(self.results, self.scores)
        self.assertEqual(report["case_count"], 3)
        first = report["cases"][0]
        self.assertEqual(first["id"], "easy-001")
        self.assertEqual(first["expected_locators"], ["C02.pdf p2"])
        self.assertEqual(first["retrieved_locators"], ["C02.pdf p2"])
        self.assertEqual(report["errors"], [])


class PickScoresTest(unittest.TestCase):
    """ragas 결과 표에서 지표를 추리는 부분."""

    def test_FactualCorrectness_세_mode를_각각_다른_지표로_받는다(self):
        # ModeMetric 은 컬럼명에 mode 를 붙여 내보낸다. 접두어로 맞추면 셋이
        # 서로를 덮어써 마지막 하나만 남는다.
        picked = _pick_scores(
            {
                "llm_context_precision_with_reference": 0.8,
                "context_recall": 0.7,
                "faithfulness": 0.9,
                "factual_correctness(mode=f1)": 0.45,
                "factual_correctness(mode=precision)": 0.30,
                "factual_correctness(mode=recall)": 0.90,
            }
        )
        self.assertEqual(
            picked,
            {
                "context_precision": 0.8,
                "context_recall": 0.7,
                "faithfulness": 0.9,
                "factual_correctness": 0.45,
                "factual_correctness_precision": 0.30,
                "factual_correctness_recall": 0.90,
            },
        )

    def test_NaN과_없는_컬럼은_버린다(self):
        picked = _pick_scores(
            {"faithfulness": float("nan"), "factual_correctness(mode=f1)": 0.5}
        )
        self.assertEqual(picked, {"factual_correctness": 0.5})


if __name__ == "__main__":
    unittest.main()
