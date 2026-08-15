"""정책-only와 RAG·LLM 대응 계획의 품질과 지연시간을 비교한다."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from statistics import mean
from typing import Protocol

import yaml

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.domain.response_policy import RISK_GRADE_CODES, PolicyRepository, ResponsePolicy
from app.dto.agent import ResponsePlanDTO
from app.dto.agent_guide import GuideSearchRequestDTO, RetrievedGuideChunkDTO


DEFAULT_RESPONSE_PLAN_EVALUATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "agent"
    / "response_plan_evaluation.yaml"
)


@dataclass(frozen=True, slots=True)
class ResponsePlanEvaluationCase:
    """한 유형과 위험등급의 대응 계획 평가 입력이다."""

    case_id: str
    fraud_type: str
    risk_grade: str
    query: str


@dataclass(frozen=True, slots=True)
class ResponsePlanEvaluationResult:
    """평가 시나리오 한 건의 전략별 측정 결과이다."""

    case_id: str
    run_number: int
    strategy: str
    fraud_type: str
    risk_grade: str
    required_action_coverage: float
    action_code_precision: float
    procedure_coverage: float
    caution_coverage: float
    output_contract_passed: bool
    fallback_used: bool
    guide_count: int
    guide_context_char_count: int
    search_latency_ms: int
    generation_latency_ms: int


@dataclass(frozen=True, slots=True)
class ResponsePlanEvaluationMetrics:
    """하나의 생성 전략에 대한 전체 평균 지표이다."""

    case_count: int
    required_action_coverage: float
    action_code_precision: float
    procedure_coverage: float
    caution_coverage: float
    output_contract_pass_rate: float
    fallback_rate: float
    average_search_latency_ms: float
    average_generation_latency_ms: float
    generation_latency_p50_ms: int
    generation_latency_p95_ms: int


@dataclass(frozen=True, slots=True)
class ResponsePlanEvaluationReport:
    """정책-only 기준과 RAG·LLM 개선 방식의 비교 보고서이다."""

    policy_only: ResponsePlanEvaluationMetrics
    rag_llm: ResponsePlanEvaluationMetrics
    results: tuple[ResponsePlanEvaluationResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class GuideSearcher(Protocol):
    def search(self, request: GuideSearchRequestDTO) -> list[RetrievedGuideChunkDTO]: ...


class ResponsePlanGenerator(Protocol):
    def generate(
        self,
        *,
        fraud_type: str,
        policy: ResponsePolicy,
        guides: list[RetrievedGuideChunkDTO],
    ) -> ResponsePlanDTO: ...


def load_response_plan_evaluation_cases(
    path: str | Path = DEFAULT_RESPONSE_PLAN_EVALUATION_PATH,
) -> tuple[ResponsePlanEvaluationCase, ...]:
    """YAML에 정의한 고정 평가 시나리오를 읽는다."""

    raw_cases = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["cases"]
    cases = tuple(ResponsePlanEvaluationCase(**item) for item in raw_cases)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("대응 계획 평가 case_id가 중복되었다.")
    for case in cases:
        if case.fraud_type not in FINAL_FRAUD_TYPE_CODES:
            raise ValueError(f"지원하지 않는 사기 유형이다: {case.fraud_type}")
        if case.risk_grade not in RISK_GRADE_CODES:
            raise ValueError(f"지원하지 않는 위험등급이다: {case.risk_grade}")
    return cases


def evaluate_response_plans(
    cases: tuple[ResponsePlanEvaluationCase, ...],
    *,
    policy_repository: PolicyRepository,
    guide_searcher: GuideSearcher,
    policy_generator: ResponsePlanGenerator,
    rag_generator: ResponsePlanGenerator,
    repeat: int = 1,
    top_k: int = 5,
) -> ResponsePlanEvaluationReport:
    """동일한 정책을 기준으로 정책-only와 RAG·LLM 생성 결과를 비교한다."""

    if repeat < 1 or top_k < 1:
        raise ValueError("repeat와 top_k는 1 이상이어야 한다.")

    results: list[ResponsePlanEvaluationResult] = []
    for case in cases:
        policy = policy_repository.get_response_policy(
            fraud_type=case.fraud_type,
            risk_grade=case.risk_grade,
        )
        search_started_at = time.perf_counter()
        guides = guide_searcher.search(
            GuideSearchRequestDTO(
                query=case.query,
                fraud_type=case.fraud_type,
                audience="MONITORING",
                risk_grade=case.risk_grade,
                action_codes=tuple(action.action_code for action in policy.actions),
                top_k=top_k,
            )
        )
        search_latency_ms = round((time.perf_counter() - search_started_at) * 1000)
        for run_number in range(1, repeat + 1):
            results.append(
                _generate_and_measure(
                    case,
                    policy,
                    run_number=run_number,
                    strategy="POLICY_ONLY",
                    generator=policy_generator,
                    guides=[],
                    search_latency_ms=0,
                )
            )
            results.append(
                _generate_and_measure(
                    case,
                    policy,
                    run_number=run_number,
                    strategy="RAG_LLM",
                    generator=rag_generator,
                    guides=guides,
                    search_latency_ms=search_latency_ms,
                )
            )

    policy_only = tuple(
        result for result in results if result.strategy == "POLICY_ONLY"
    )
    rag_llm = tuple(result for result in results if result.strategy == "RAG_LLM")
    return ResponsePlanEvaluationReport(
        policy_only=_calculate_metrics(policy_only),
        rag_llm=_calculate_metrics(rag_llm),
        results=tuple(results),
    )


def _generate_and_measure(
    case: ResponsePlanEvaluationCase,
    policy: ResponsePolicy,
    *,
    run_number: int,
    strategy: str,
    generator: ResponsePlanGenerator,
    guides: list[RetrievedGuideChunkDTO],
    search_latency_ms: int,
) -> ResponsePlanEvaluationResult:
    started_at = time.perf_counter()
    plan = generator.generate(
        fraud_type=case.fraud_type,
        policy=policy,
        guides=guides,
    )
    generation_latency_ms = round((time.perf_counter() - started_at) * 1000)

    policy_codes = {action.action_code for action in policy.actions}
    required_codes = {
        action.action_code for action in policy.actions if action.required
    }
    generated_codes = {action.action_code for action in plan.recommended_actions}
    action_count = len(plan.recommended_actions)
    required_coverage = len(required_codes & generated_codes) / len(required_codes)
    code_precision = (
        len(policy_codes & generated_codes) / action_count if action_count else 0.0
    )
    procedure_coverage = (
        sum(bool(action.procedure_steps) for action in plan.recommended_actions)
        / action_count
        if action_count
        else 0.0
    )
    caution_coverage = (
        sum(bool(action.cautions) for action in plan.recommended_actions)
        / action_count
        if action_count
        else 0.0
    )
    fallback_used = strategy == "RAG_LLM" and procedure_coverage == 0.0

    return ResponsePlanEvaluationResult(
        case_id=case.case_id,
        run_number=run_number,
        strategy=strategy,
        fraud_type=case.fraud_type,
        risk_grade=case.risk_grade,
        required_action_coverage=round(required_coverage, 4),
        action_code_precision=round(code_precision, 4),
        procedure_coverage=round(procedure_coverage, 4),
        caution_coverage=round(caution_coverage, 4),
        output_contract_passed=(
            plan.applied_fraud_type == case.fraud_type
            and generated_codes == policy_codes
        ),
        fallback_used=fallback_used,
        guide_count=len(guides),
        guide_context_char_count=sum(len(guide.content) for guide in guides),
        search_latency_ms=search_latency_ms,
        generation_latency_ms=generation_latency_ms,
    )


def _calculate_metrics(
    results: tuple[ResponsePlanEvaluationResult, ...],
) -> ResponsePlanEvaluationMetrics:
    generation_latencies = sorted(r.generation_latency_ms for r in results)
    return ResponsePlanEvaluationMetrics(
        case_count=len(results),
        required_action_coverage=round(
            mean(r.required_action_coverage for r in results), 4
        ),
        action_code_precision=round(mean(r.action_code_precision for r in results), 4),
        procedure_coverage=round(mean(r.procedure_coverage for r in results), 4),
        caution_coverage=round(mean(r.caution_coverage for r in results), 4),
        output_contract_pass_rate=round(
            mean(r.output_contract_passed for r in results), 4
        ),
        fallback_rate=round(mean(r.fallback_used for r in results), 4),
        average_search_latency_ms=round(mean(r.search_latency_ms for r in results), 2),
        average_generation_latency_ms=round(
            mean(r.generation_latency_ms for r in results), 2
        ),
        generation_latency_p50_ms=_percentile(generation_latencies, 0.50),
        generation_latency_p95_ms=_percentile(generation_latencies, 0.95),
    )


def _percentile(values: list[int], percentile: float) -> int:
    """작은 평가 세트에서도 해석하기 쉬운 nearest-rank 값을 반환한다."""

    return values[max(ceil(len(values) * percentile) - 1, 0)]


__all__ = [
    "DEFAULT_RESPONSE_PLAN_EVALUATION_PATH",
    "ResponsePlanEvaluationCase",
    "ResponsePlanEvaluationMetrics",
    "ResponsePlanEvaluationReport",
    "ResponsePlanEvaluationResult",
    "evaluate_response_plans",
    "load_response_plan_evaluation_cases",
]
