"""골든셋으로 실제 RAG 경로를 돌리고 채점 지표를 산출한다.

RAGAS 지표는 4개다.
    - LLMContextPrecisionWithReference : 검색된 청크가 정답과 얼마나 관련 있나
    - LLMContextRecall                 : 정답의 주장들이 검색 청크에 얼마나 담겼나
    - Faithfulness                     : 생성 답변이 검색 청크를 벗어나지 않았나
    - FactualCorrectness               : 생성 답변이 모범 답변과 사실관계가 맞나

FactualCorrectness 는 mode 를 셋 다 돌려 **precision 과 recall 을 따로 남긴다**. f1 하나만
남기면 "필수 정보를 빠뜨렸다"와 "맞는 말을 더 했다"가 한 숫자에 섞여 구별되지 않는데,
고객 응대에서 이 둘은 무게가 전혀 다르다. 실측(골든셋 v1)에서 FP 222개 중 보일러플레이트는
10개뿐이고 나머지는 대체로 맞는 안내였다 — 즉 f1 하락분의 상당 부분이 벌할 이유가 없는
precision 손실이었다. 어느 쪽이 깎였는지 보이게 두고 판단은 사람이 한다.

파생 지표는 두지 않는다. 사례별로 실제 검색된 (문서, 페이지)와 정답 근거의 (문서, 페이지)를
리포트에 그대로 남겨 필요할 때 눈으로 대조한다.

무근거(unanswerable) 사례는 모든 지표를 평균에서 제외하고 abstain_rate 로만 본다.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sqlmodel import Session

from app.dto.chatbot import ExtractedGuideSearchQuery, RetrievedChatbotGuideChunkDTO
from app.services.chatbot.extractors import GuideSearchQueryExtractor
from app.services.chatbot.guide_responder import (
    GUIDE_SEARCH_QUERY_HEADING_PREFIX,
    RETRIEVE_TOP_K,
    GuideResponder,
)
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.rag.chatbot_retriever import retriever_source
from app.services.rag.golden_dataset import ANSWERABLE_CATEGORIES, GoldenCase
from app.services.rag.ragas_judge import build_judge_llm

logger = logging.getLogger(__name__)

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
    """실제 리트리버를 그대로 호출하면서 반환 청크를 기록한다.

    GuideResponder 가 retriever 주입을 지원하므로 이것만 끼우면 챗봇 코드를
    건드리지 않고 질의별 검색 결과를 모을 수 있다.
    """

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
        """고객이 받은 응답에 안내가 하나도 담기지 않은 경우.

        질의가 여러 개면 일부만 근거를 찾는 일이 흔하다. 그때 응답에는 답변 섹션과
        B.5 문구가 함께 들어가는데, 이를 기권으로 세면 절반은 답한 턴까지 기권으로
        잡혀 abstain_rate 가 부풀려진다. 그래서 문구 포함 여부가 아니라 **모든**
        섹션이 B.5 문구인지로 판정한다.

        근거를 찾은 질의 수(grounded_query_count)로 판정하면 안 된다. 검색이 성공해도
        생성 LLM 이 그 자리의 안내를 내놓지 못하면 GuideResponder 는 B.5 문구로
        메워 내보내므로(guide_responder._assemble), 고객은 기권을 받았는데 지표는
        답변으로 세는 일이 생긴다. 실측 100건에서 5건이 여기 걸렸다.
        """

        if not self.response.strip():
            return True
        return not _has_guidance(self.response)


def _has_guidance(message_text: str) -> bool:
    """응답 본문에 B.5 문구가 아닌 안내가 한 섹션이라도 있는가.

    GuideResponder 는 소제목 한 줄과 본문을 붙여 섹션을 만든다. 소제목을 걷어낸
    나머지가 전부 B.5 문구면 고객이 받은 것은 기권 응답이다.
    """

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
    extractor: GuideSearchQueryExtractor | None = None,
    retriever: RetrieverCallable = retriever_source,
    responder_factory: Callable[[Any, int], Any] = _default_responder,
    top_k: int = RETRIEVE_TOP_K,
    on_case: CaseCallback | None = None,
) -> list[RunResult]:
    """골든셋 질문을 실제 RAG 경로에 태워 응답과 검색 청크를 모은다.

    extractor / retriever / responder_factory 는 테스트에서 LLM·DB 호출을
    대체하려고 열어 둔다. 기본값이 곧 운영 경로다.
    on_case 는 사례가 끝날 때마다 불린다(진행 상황 출력용).
    """

    extractor = extractor or GuideSearchQueryExtractor()
    total = len(cases)
    results = []
    for index, case in enumerate(cases, start=1):
        started = time.monotonic()
        result = _run_case(
            case,
            session,
            extractor=extractor,
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
    extractor: GuideSearchQueryExtractor,
    retriever: RetrieverCallable,
    responder_factory: Callable[[Any, int], Any],
    top_k: int,
) -> RunResult:
    recorder = _RecordingRetriever(retriever=retriever)
    responder = responder_factory(recorder, top_k)

    try:
        extraction = extractor.extract(user_answers=case.user_input)
    except Exception as exc:  # 분해 실패는 그 턴 전체가 안내 없이 끝난다
        logger.warning("질의 분해 실패: id=%s error=%s", case.id, type(exc).__name__)
        return _empty_result(case, error=f"extract:{type(exc).__name__}")

    queries: list[ExtractedGuideSearchQuery] = list(extraction.guide_search_queries)
    if not queries:
        # 분해 결과가 없는 턴은 본문이 비므로 메시지를 보내지 않는다(PRD 2.5).
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

    # ragas 는 eval 전용 의존성(uv sync --group eval)이라 함수 안에서 import 한다.
    #
    # ragas.metrics 의 클래스들은 "ragas.metrics.collections 를 쓰라"는 폐기 경고를
    # 내지만, 0.4.3 의 evaluate() 는 ragas.metrics.base.Metric 만 받는다. collections
    # 쪽은 BaseMetric 이라는 별도 계층이라 evaluate() 에 넣으면 TypeError 가 난다
    # ("All metrics must be initialised metric objects"). 같은 이유로 심판 LLM 도
    # llm_factory(InstructorLLM)가 아니라 BaseRagasLLM 인 LangchainLLMWrapper 여야
    # 한다. ragas 가 v1.0 에서 evaluate() 를 새 계층으로 옮기면 그때 함께 바꾼다.
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

    # FactualCorrectness 는 mode 를 셋 다 돌린다. f1 하나로는 "필수 정보를 빠뜨렸다"
    # (recall 손실)와 "맞는 말을 더 했다"(precision 손실)가 구별되지 않는다.
    # precision 은 한 방향만 판정하지만 recall 은 양방향이 다 필요해(_single_turn_ascore
    # 가 fn 을 반대 방향에서 센다) 심판 호출이 f1 단독 대비 2.5배로 늘어난다.
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
) -> dict[str, Any]:
    """전체 평균과 category·difficulty 슬라이스를 모은다."""

    return {
        "case_count": len(results),
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
    results = run_cases(cases, session, top_k=top_k, on_case=on_case)

    notify(f"[2/3] RAGAS 채점 ({len(cases)}건 x 지표 6개) — 심판 LLM 호출")
    scores = score_with_ragas(results)

    notify("[3/3] 리포트 집계")
    return build_report(results, scores)


__all__ = [
    "ANSWERABLE_METRICS",
    "CaseCallback",
    "PhaseCallback",
    "RunResult",
    "build_report",
    "evaluate_rag",
    "run_cases",
    "score_with_ragas",
]
