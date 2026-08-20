"""대응 가이드 검색 품질을 반복 측정하기 위한 평가 질의를 로딩한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.domain.agent_guide import GuideDocumentValidationError
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.dto.agent_guide import GuideSearchRequestDTO, RetrievedGuideChunkDTO
from app.services.agent.guide_corpus import (
    ALLOWED_AUDIENCES,
    ALLOWED_RISK_GRADES,
)
from app.services.agent.guide_search import GuideSearchService


DEFAULT_GUIDE_EVALUATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "agent"
    / "guide_retrieval_evaluation.yaml"
)
NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class GuideRetrievalEvaluationCase:
    """하나의 검색 질문과 검색 결과에 포함되어야 하는 문서 계약이다."""

    query_id: str
    query: str
    fraud_type: str
    audience: str
    risk_grade: str
    action_codes: tuple[str, ...]
    expected_document_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """여러 평가 질의에서 계산한 문서 검색 품질 지표이다."""

    query_count: int
    precision_at_1: float
    hit_rate_at_3: float
    hit_rate_at_5: float
    mrr: float


@dataclass(frozen=True, slots=True)
class GuideSearchEvaluationReport:
    """필터 없는 벡터 검색과 메타데이터 결합 검색의 비교 결과이다."""

    baseline: RetrievalMetrics
    filtered: RetrievalMetrics
    by_fraud_type: dict[str, dict[str, RetrievalMetrics]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_csv_rows(self) -> list[dict[str, object]]:
        """검색 전략·유형별 집계 지표를 CSV 행으로 변환한다."""
        rows = [
            _metrics_to_row(scope="ALL", strategy="BASELINE", metrics=self.baseline),
            _metrics_to_row(scope="ALL", strategy="FILTERED", metrics=self.filtered),
        ]
        for fraud_type, metrics_by_strategy in self.by_fraud_type.items():
            for strategy, metrics in metrics_by_strategy.items():
                rows.append(
                    _metrics_to_row(
                        scope=fraud_type,
                        strategy=strategy.upper(),
                        metrics=metrics,
                    )
                )
        return rows


class _EvaluationCaseSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    query_id: NonEmptyText
    query: NonEmptyText
    fraud_type: NonEmptyText
    audience: NonEmptyText
    risk_grade: NonEmptyText
    action_codes: tuple[NonEmptyText, ...] = Field(min_length=1)
    expected_document_ids: tuple[NonEmptyText, ...] = Field(min_length=1)


class _EvaluationDocumentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: tuple[_EvaluationCaseSchema, ...] = Field(min_length=1)


def load_guide_evaluation_cases(
    path: str | Path = DEFAULT_GUIDE_EVALUATION_PATH,
) -> tuple[GuideRetrievalEvaluationCase, ...]:
    """YAML 평가 세트를 검증하여 검색 평가에서 재사용할 불변 객체로 만든다."""

    evaluation_path = Path(path)
    try:
        raw_document = yaml.safe_load(evaluation_path.read_text(encoding="utf-8"))
        validated = _EvaluationDocumentSchema.model_validate(raw_document)
    except FileNotFoundError:
        raise
    except yaml.YAMLError as error:
        raise GuideDocumentValidationError(
            f"검색 평가 YAML 형식이 올바르지 않다: {evaluation_path}"
        ) from error
    except ValidationError as error:
        raise GuideDocumentValidationError(
            f"검색 평가 데이터가 올바르지 않다: {error}"
        ) from error

    cases = tuple(
        GuideRetrievalEvaluationCase(
            query_id=item.query_id,
            query=item.query,
            fraud_type=item.fraud_type,
            audience=item.audience,
            risk_grade=item.risk_grade,
            action_codes=item.action_codes,
            expected_document_ids=item.expected_document_ids,
        )
        for item in validated.cases
    )
    _validate_evaluation_contract(cases)
    return cases


def _validate_evaluation_contract(
    cases: tuple[GuideRetrievalEvaluationCase, ...],
) -> None:
    query_ids = [case.query_id for case in cases]
    if len(query_ids) != len(set(query_ids)):
        raise GuideDocumentValidationError("검색 평가 query_id가 중복되었다.")

    for case in cases:
        if case.fraud_type not in FINAL_FRAUD_TYPE_CODES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 사기 유형이 있다: {case.query_id}"
            )
        if case.audience not in ALLOWED_AUDIENCES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 대상 코드가 있다: {case.query_id}"
            )
        if case.risk_grade not in ALLOWED_RISK_GRADES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 위험등급이 있다: {case.query_id}"
            )


def evaluate_guide_search(
    cases: tuple[GuideRetrievalEvaluationCase, ...],
    search_service: GuideSearchService,
) -> GuideSearchEvaluationReport:
    """고정 질의로 기준 검색과 메타데이터 결합 검색을 함께 평가한다."""

    baseline_results: dict[str, list[str]] = {}
    filtered_results: dict[str, list[str]] = {}
    expected: dict[str, set[str]] = {}

    for case in cases:
        request = GuideSearchRequestDTO(
            query=case.query,
            fraud_type=case.fraud_type,
            audience=case.audience,
            risk_grade=case.risk_grade,
            action_codes=case.action_codes,
            top_k=5,
        )
        baseline, filtered = search_service.compare(request)
        baseline_results[case.query_id] = _unique_document_ids(baseline)
        filtered_results[case.query_id] = _unique_document_ids(filtered)
        expected[case.query_id] = set(case.expected_document_ids)

    by_fraud_type: dict[str, dict[str, RetrievalMetrics]] = {}
    for fraud_type in sorted({case.fraud_type for case in cases}):
        query_ids = [case.query_id for case in cases if case.fraud_type == fraud_type]
        by_fraud_type[fraud_type] = {
            "baseline": calculate_retrieval_metrics(
                {query_id: baseline_results[query_id] for query_id in query_ids},
                {query_id: expected[query_id] for query_id in query_ids},
            ),
            "filtered": calculate_retrieval_metrics(
                {query_id: filtered_results[query_id] for query_id in query_ids},
                {query_id: expected[query_id] for query_id in query_ids},
            ),
        }

    return GuideSearchEvaluationReport(
        baseline=calculate_retrieval_metrics(baseline_results, expected),
        filtered=calculate_retrieval_metrics(filtered_results, expected),
        by_fraud_type=by_fraud_type,
    )


def calculate_retrieval_metrics(
    results: dict[str, list[str]],
    expected: dict[str, set[str]],
) -> RetrievalMetrics:
    """문서 단위 Precision@1, Hit@3/5, MRR을 계산한다."""

    reciprocal_ranks: list[float] = []
    hits_at_1 = hits_at_3 = hits_at_5 = 0
    for query_id, relevant_ids in expected.items():
        ranked_ids = results.get(query_id, [])
        hits_at_1 += int(bool(relevant_ids.intersection(ranked_ids[:1])))
        hits_at_3 += int(bool(relevant_ids.intersection(ranked_ids[:3])))
        hits_at_5 += int(bool(relevant_ids.intersection(ranked_ids[:5])))
        first_rank = next(
            (
                rank
                for rank, document_id in enumerate(ranked_ids, start=1)
                if document_id in relevant_ids
            ),
            None,
        )
        reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)

    count = len(expected)
    return RetrievalMetrics(
        query_count=count,
        precision_at_1=round(hits_at_1 / count, 4),
        hit_rate_at_3=round(hits_at_3 / count, 4),
        hit_rate_at_5=round(hits_at_5 / count, 4),
        mrr=round(sum(reciprocal_ranks) / count, 4),
    )


def _unique_document_ids(results: list[RetrievedGuideChunkDTO]) -> list[str]:
    """같은 문서의 여러 Chunk가 평가 순위를 중복 점유하지 않게 한다."""

    return list(dict.fromkeys(result.document_id for result in results))


def _metrics_to_row(
    *,
    scope: str,
    strategy: str,
    metrics: RetrievalMetrics,
) -> dict[str, object]:
    return {"scope": scope, "strategy": strategy, **asdict(metrics)}


__all__ = [
    "DEFAULT_GUIDE_EVALUATION_PATH",
    "GuideSearchEvaluationReport",
    "GuideRetrievalEvaluationCase",
    "RetrievalMetrics",
    "calculate_retrieval_metrics",
    "evaluate_guide_search",
    "load_guide_evaluation_cases",
]
