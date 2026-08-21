"""골든셋으로 챗봇 RAG 경로를 실행하고 RAGAS 지표를 산출한다.

답변 가능 사례는 검색 정밀도·재현율, 충실성, 사실 정확도를 계산한다. 무근거 사례는
지표 평균에서 제외하고 기권율로 집계한다.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from sqlmodel import Session

from app.dto.chatbot import ExtractedGuideSearchQuery, RetrievedChatbotGuideChunkDTO
from app.services.chatbot.answer_analyzer import AnswerAnalyzer
from app.services.chatbot.guide_responder import (
    GUIDE_SEARCH_QUERY_HEADING_PREFIX,
    RETRIEVE_TOP_K,
    GuideResponder,
)
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.rag.chatbot_retriever import retriever_source
from app.services.rag.golden_dataset import ANSWERABLE_CATEGORIES, GoldenCase
from app.services.rag.ragas_judge import build_judge_llm
from app.services.rag.token_pricing import build_usage_report, format_usage_summary

logger = logging.getLogger(__name__)

# 골든셋의 user_input을 운영 턴의 customer_answer 자리에 넣을 때 사용하는 직전 질문.
# 답변 가능 여부와 targeted query 생성을 운영과 같은 통합 분석기로 평가한다.
RAG_EVALUATION_QUESTION = (
    "금융사기 대응과 관련해 겪은 상황이나 궁금한 점을 구체적으로 말씀해 주세요."
)

# 모든 지표를 답변 가능 사례에서만 평균낸다. 무근거 사례는 abstain_rate 로 본다.
ANSWERABLE_METRICS = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "factual_correctness",
    "factual_correctness_precision",
    "factual_correctness_recall",
)

# ragas 가 결과 표에 쓰는 컬럼명 → 리포트에서 쓰는 이름.
# ModeMetric 은 설정을 컬럼명에 붙여 내보내므로(evaluation.py 의 f"{name}(mode={mode})")
# FactualCorrectness 세 벌은 서로 다른 컬럼으로 나온다.
METRIC_COLUMNS = {
    "llm_context_precision_with_reference": "context_precision",
    "context_recall": "context_recall",
    "faithfulness": "faithfulness",
    "factual_correctness(mode=f1)": "factual_correctness",
    "factual_correctness(mode=precision)": "factual_correctness_precision",
    "factual_correctness(mode=recall)": "factual_correctness_recall",
}


RetrieverCallable = Callable[..., list[RetrievedChatbotGuideChunkDTO]]

# 사례 하나가 끝날 때마다 (순번, 전체, 결과) 로 불린다. 진행 상황 출력용이며
# 서비스 계층이 직접 print 하지 않도록 호출자가 넘긴다.
CaseCallback = Callable[[int, int, "RunResult"], None]
# 단계 전환 알림 (예: "파이프라인 실행", "RAGAS 채점")
PhaseCallback = Callable[[str], None]


@dataclass
class _RecordingRetriever:
    """검색 결과를 기록하면서 실제 리트리버를 호출한다."""

    retriever: RetrieverCallable = retriever_source
    chunks: list[RetrievedChatbotGuideChunkDTO] = field(default_factory=list)

    def __call__(
        self,
        question: str,
        session: Session,
        top_k: int = RETRIEVE_TOP_K,
    ) -> list[RetrievedChatbotGuideChunkDTO]:
        found = self.retriever(question, session, top_k=top_k)
        self.chunks.extend(found)
        return found


@dataclass(frozen=True, slots=True)
class RunResult:
    """사례 하나를 실제 파이프라인에 태운 결과."""

    case: GoldenCase
    response: str
    retrieved_contexts: tuple[str, ...]
    retrieved_locators: tuple[tuple[str, int], ...]
    search_queries: tuple[str, ...]
    error: str | None = None
    elapsed_seconds: float = 0.0
    grounded_query_count: int = 0

    @property
    def abstained(self) -> bool:
        """응답의 모든 섹션이 무근거 안내이면 참을 반환한다."""

        if not self.response.strip():
            return True
        return not _has_guidance(self.response)


def _has_guidance(message_text: str) -> bool:
    """응답에 무근거 안내가 아닌 본문이 있는지 확인한다."""

    head, *sections = message_text.split(GUIDE_SEARCH_QUERY_HEADING_PREFIX)
    # 소제목이 붙은 섹션은 첫 줄이 제목이라 본문에서 뺀다. 소제목보다 앞에 있는
    # 조각(head)은 제목이 없으므로 통째로 본문이다.
    bodies = [head, *("\n".join(s.splitlines()[1:]) for s in sections)]
    return any(
        body.strip() and body.strip() != UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
        for body in bodies
    )


def _default_responder(recorder: "_RecordingRetriever", top_k: int) -> GuideResponder:
    """검색 결과를 기록하는 리트리버를 끼운 실제 GuideResponder."""

    return GuideResponder(retriever=recorder, top_k=top_k)


def run_cases(
    cases: tuple[GoldenCase, ...],
    session: Session,
    *,
    analyzer: AnswerAnalyzer | None = None,
    retriever: RetrieverCallable = retriever_source,
    responder_factory: Callable[[Any, int], Any] = _default_responder,
    top_k: int = RETRIEVE_TOP_K,
    on_case: CaseCallback | None = None,
) -> list[RunResult]:
    """골든셋 질문을 실제 RAG 경로에 태워 응답과 검색 청크를 모은다.

    analyzer / retriever / responder_factory 는 테스트에서 LLM·DB 호출을
    대체하려고 열어 둔다. 기본값이 곧 운영 경로다.
    on_case 는 사례가 끝날 때마다 불린다(진행 상황 출력용).
    """

    analyzer = analyzer or AnswerAnalyzer()
    total = len(cases)
    results = []
    for index, case in enumerate(cases, start=1):
        started = time.monotonic()
        result = _run_case(
            case,
            session,
            analyzer=analyzer,
            retriever=retriever,
            responder_factory=responder_factory,
            top_k=top_k,
        )
        result = replace(result, elapsed_seconds=time.monotonic() - started)
        results.append(result)
        if on_case is not None:
            on_case(index, total, result)
    return results


def _run_case(
    case: GoldenCase,
    session: Session,
    *,
    analyzer: AnswerAnalyzer,
    retriever: RetrieverCallable,
    responder_factory: Callable[[Any, int], Any],
    top_k: int,
) -> RunResult:
    recorder = _RecordingRetriever(retriever=retriever)
    responder = responder_factory(recorder, top_k)

    try:
        analysis = analyzer.analyze(
            question_text=RAG_EVALUATION_QUESTION,
            customer_answer=case.user_input,
        )
    except Exception as exc:  # 분석 실패는 그 턴 전체가 안내 없이 끝난다
        logger.warning("통합 분석 실패: id=%s error=%s", case.id, type(exc).__name__)
        return _empty_result(case, error=f"analyze:{type(exc).__name__}")

    if analysis.quality_verdict is None:
        reason = analysis.verdict_skip_reason or "UNKNOWN"
        logger.warning("통합 분석 실패: id=%s reason=%s", case.id, reason)
        return _empty_result(case, error=f"analyze:{reason}")

    queries: list[ExtractedGuideSearchQuery] = list(analysis.guide_search_queries)
    if not queries:
        # 검색 질의가 없으면 가이드 메시지를 만들지 않는다.
        return _empty_result(case)

    try:
        response = responder.respond(guide_search_queries=queries, session=session)
        message_text = response.message_text
    except Exception as exc:
        logger.warning("가이드 응답 조립 실패: id=%s error=%s", case.id, type(exc).__name__)
        return _empty_result(
            case,
            search_queries=tuple(q.search_query for q in queries),
            error=f"respond:{type(exc).__name__}",
        )

    return RunResult(
        case=case,
        response=message_text,
        grounded_query_count=len(response.grounded_query_positions),
        retrieved_contexts=tuple(chunk.content for chunk in recorder.chunks),
        retrieved_locators=tuple(
            (chunk.source_title, chunk.page)
            for chunk in recorder.chunks
            if chunk.page is not None
        ),
        search_queries=tuple(q.search_query for q in queries),
    )


def _empty_result(
    case: GoldenCase,
    *,
    search_queries: tuple[str, ...] = (),
    error: str | None = None,
) -> RunResult:
    return RunResult(
        case=case,
        response="",
        retrieved_contexts=(),
        retrieved_locators=(),
        search_queries=search_queries,
        error=error,
    )


def score_with_ragas(results: list[RunResult]) -> dict[str, dict[str, float]]:
    """RAGAS 지표를 사례별로 산출한다. 반환값은 {case_id: {지표: 점수}}.

    FactualCorrectness 는 mode 별로 다른 지표 이름을 받는다(METRIC_COLUMNS).
    """

    # eval 전용 의존성이므로 함수 안에서 import한다. evaluate() 계약에 맞는
    # ragas.metrics.base.Metric 구현을 사용해야 한다.
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import (
        FactualCorrectness,
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
    )

    from app.services.rag.ragas_judge import build_claim_decomposition_prompt

    samples = [
        {
            "user_input": result.case.user_input,
            "retrieved_contexts": list(result.retrieved_contexts),
            "response": result.response,
            "reference": result.case.reference,
        }
        for result in results
    ]

    # 누락과 불필요한 추가 정보를 구분하기 위해 세 mode를 모두 계산한다.
    scores = evaluate(
        dataset=EvaluationDataset.from_list(samples),
        metrics=[
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
            Faithfulness(),
            FactualCorrectness(
                mode="f1", claim_decomposition_prompt=build_claim_decomposition_prompt()
            ),
            FactualCorrectness(
                mode="precision",
                claim_decomposition_prompt=build_claim_decomposition_prompt(),
            ),
            FactualCorrectness(
                mode="recall", claim_decomposition_prompt=build_claim_decomposition_prompt()
            ),
        ],
        llm=build_judge_llm(),
    )

    rows = scores.to_pandas().to_dict(orient="records")
    if rows:
        _warn_missing_columns(rows[0])
    return {
        result.case.id: _pick_scores(row)
        for result, row in zip(results, rows)
    }


def _warn_missing_columns(row: dict) -> None:
    """기대한 컬럼이 결과 표에 없으면 알린다.

    METRIC_COLUMNS 는 ragas 가 만드는 컬럼명을 그대로 적어둔 것이라, ragas 가 이름을
    바꾸면 점수가 조용히 사라진다. 비싼 실행을 마친 뒤 리포트가 비어 있는 것을 보고
    알아채는 일이 없도록 여기서 경고한다.
    """

    missing = [column for column in METRIC_COLUMNS if column not in row]
    if missing:
        logger.warning(
            "RAGAS 결과에 없는 컬럼: %s (실제 컬럼: %s)", missing, sorted(row)
        )


def _pick_scores(row: dict) -> dict[str, float]:
    """ragas 결과 한 행에서 지표 점수만 추린다. NaN 은 버린다.

    ModeMetric 은 설정을 컬럼명에 붙여 내보낸다(예: "factual_correctness(mode=f1)").
    METRIC_COLUMNS 의 키가 그 컬럼명 그대로라 여기서는 정확히 일치하는 것만 본다.
    접두어로 맞추면 FactualCorrectness 세 벌이 서로를 덮어쓴다.
    """

    picked = {}
    for column, name in METRIC_COLUMNS.items():
        value = _as_float(row.get(column))
        if value is not None:
            picked[name] = value
    return picked


def build_report(
    results: list[RunResult],
    scores: dict[str, dict[str, float]],
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """전체 평균과 category·difficulty 슬라이스를 모은다.

    usage 를 주면 토큰·비용 집계(build_usage_section)를 리포트 앞쪽에 함께 담는다.
    한 번 돌리는 데 실제 비용이 드는 실행이라, 점수와 비용을 같은 파일에 남겨야
    "지표가 이만큼 오르는 데 얼마가 들었나"를 나중에 대조할 수 있다.
    """

    report: dict[str, Any] = {"case_count": len(results)}
    if usage is not None:
        report["usage"] = usage
    report.update({
        "overall": _aggregate(results, scores),
        "by_category": {
            category: _aggregate(rows, scores)
            for category, rows in _group(results, lambda r: r.case.category).items()
        },
        "by_difficulty": {
            difficulty: _aggregate(rows, scores)
            for difficulty, rows in _group(results, lambda r: r.case.difficulty).items()
        },
        "errors": [
            {"id": r.case.id, "error": r.error} for r in results if r.error
        ],
        "cases": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "search_queries": list(r.search_queries),
                "retrieved_locators": [f"{t} p{p}" for t, p in r.retrieved_locators],
                "expected_locators": [
                    f"{t} p{p}" for t, p in sorted(r.case.locators())
                ],
                "abstained": r.abstained,
                "grounded_query_count": r.grounded_query_count,
                "elapsed_seconds": round(r.elapsed_seconds, 2),
                "scores": scores.get(r.case.id, {}),
                "response": r.response,
            }
            for r in results
        ],
    })
    return report


def build_usage_section(
    pipeline_usage: dict[str, Any],
    judge_usage: dict[str, Any],
    case_count: int,
) -> dict[str, Any]:
    """파이프라인 비용과 RAGAS 심판 비용을 나눠 담은 usage 섹션.

    둘을 합쳐 놓으면 안 된다. pipeline 은 운영에서 고객 한 턴에 실제로 나가는 비용이고,
    judge 는 평가할 때만 드는 비용(사례당 지표 6개 호출)이라 성격이 전혀 다르다.
    total 은 이번 실행에 실제로 청구될 금액을 확인하는 용도다.
    """

    pipeline = build_usage_report(pipeline_usage, case_count=case_count)
    judge = build_usage_report(judge_usage, case_count=case_count)
    return {
        "pipeline": pipeline,
        "judge": judge,
        "total_cost_usd": round(
            pipeline["total_cost_usd"] + judge["total_cost_usd"], 6
        ),
        "total_cost_known": pipeline["total_cost_known"] and judge["total_cost_known"],
    }


def _aggregate(
    results: list[RunResult],
    scores: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """한 묶음의 지표 평균. 검색 지표는 근거가 있는 사례만 평균낸다."""

    summary: dict[str, Any] = {"count": len(results)}
    answerable = [r for r in results if r.case.category in ANSWERABLE_CATEGORIES]

    for metric in ANSWERABLE_METRICS:
        summary[metric] = _mean(
            scores.get(r.case.id, {}).get(metric) for r in answerable
        )

    summary["abstain_rate"] = _mean(float(r.abstained) for r in results)
    summary["avg_elapsed_seconds"] = _mean(r.elapsed_seconds for r in results)
    summary["scored_count"] = len(answerable)
    return summary


def _group(results, key):
    grouped = defaultdict(list)
    for result in results:
        grouped[key(result)].append(result)
    return dict(grouped)


def _mean(values) -> float | None:
    collected = [v for v in values if v is not None]
    if not collected:
        return None
    return round(statistics.fmean(collected), 4)


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN 제외


def evaluate_rag(
    cases: tuple[GoldenCase, ...],
    session: Session,
    *,
    top_k: int = RETRIEVE_TOP_K,
    on_case: CaseCallback | None = None,
    on_phase: PhaseCallback | None = None,
) -> dict[str, Any]:
    """골든셋 실행 → RAGAS 채점 → 슬라이스 리포트까지 한 번에 수행한다."""

    def notify(message: str) -> None:
        if on_phase is not None:
            on_phase(message)

    notify(f"[1/3] 파이프라인 실행 ({len(cases)}건) — 질의 분해·검색·응답 생성")
    with get_usage_metadata_callback() as pipeline_usage:
        results = run_cases(cases, session, top_k=top_k, on_case=on_case)
    pipeline_usage_by_model = dict(pipeline_usage.usage_metadata)
    notify(format_usage_summary(pipeline_usage_by_model))

    notify(f"[2/3] RAGAS 채점 ({len(cases)}건 x 지표 6개) — 심판 LLM 호출")
    # 심판 비용은 파이프라인 비용과 섞지 않는다. ragas 는 asyncio.run 으로 같은
    # 스레드에서 돌아 컨텍스트 변수가 그대로 이어지므로 이 콜백에 심판 호출이 잡힌다.
    with get_usage_metadata_callback() as judge_usage:
        scores = score_with_ragas(results)
    judge_usage_by_model = dict(judge_usage.usage_metadata)

    notify("[3/3] 리포트 집계")
    return build_report(
        results,
        scores,
        usage=build_usage_section(
            pipeline_usage_by_model, judge_usage_by_model, len(results)
        ),
    )


__all__ = [
    "ANSWERABLE_METRICS",
    "CaseCallback",
    "PhaseCallback",
    "RunResult",
    "build_report",
    "build_usage_section",
    "evaluate_rag",
    "run_cases",
    "score_with_ragas",
]
