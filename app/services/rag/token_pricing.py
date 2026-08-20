"""OpenAI 모델별 토큰 단가와 사용량 → 비용 추정.

단가는 https://developers.openai.com/api/docs/pricing 를 2026-08-19 에 직접
확인해 옮겨 적은 값이다(짧은 컨텍스트 기준, 1M 토큰당 USD). OpenAI 가 가격을
바꾸면 이 표도 손으로 같이 갱신해야 한다 — 자동으로 따라가지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages.ai import UsageMetadata


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """1M 토큰당 USD 단가."""

    input: float
    cached_input: float
    output: float


# 키가 길수록 더 구체적인 모델명이므로, resolve_pricing 에서 긴 키부터 매칭한다.
MODEL_PRICING_PER_1M: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing(input=0.15, cached_input=0.075, output=0.60),
    "gpt-4o": ModelPricing(input=2.50, cached_input=1.25, output=10.00),
    "gpt-4.1-nano": ModelPricing(input=0.10, cached_input=0.025, output=0.40),
    "gpt-4.1-mini": ModelPricing(input=0.40, cached_input=0.10, output=1.60),
    "gpt-4.1": ModelPricing(input=2.00, cached_input=0.50, output=8.00),
    "gpt-5-nano": ModelPricing(input=0.05, cached_input=0.005, output=0.40),
    "gpt-5-mini": ModelPricing(input=0.25, cached_input=0.025, output=2.00),
    "gpt-5": ModelPricing(input=1.25, cached_input=0.125, output=10.00),
    "gpt-5.6-luna": ModelPricing(input=0.20, cached_input=0.02, output=1.20),
    "gpt-5.6-terra": ModelPricing(input=2.00, cached_input=0.20, output=12.00),
    "gpt-5.6-sol": ModelPricing(input=5.00, cached_input=0.50, output=30.00),
    "text-embedding-3-small": ModelPricing(input=0.02, cached_input=0.02, output=0.0),
}

# 매칭 우선순위용으로 한 번만 정렬해 둔다 (예: "gpt-4.1-mini" 가 "gpt-4.1" 보다 먼저 걸려야 함).
_PRICING_KEYS_BY_LENGTH_DESC = sorted(MODEL_PRICING_PER_1M, key=len, reverse=True)


def resolve_pricing(model_name: str) -> ModelPricing | None:
    """API 가 돌려준 모델명(버전 접미사 포함 가능)을 단가표에 매칭한다.

    예: "gpt-4o-mini-2024-07-18" -> "gpt-4o-mini" 단가.
    """

    for key in _PRICING_KEYS_BY_LENGTH_DESC:
        if model_name.startswith(key):
            return MODEL_PRICING_PER_1M[key]
    return None


@dataclass(frozen=True, slots=True)
class ModelCostEstimate:
    model_name: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float | None  # 단가표에 없는 모델이면 None (가격을 모른다는 뜻)


def estimate_costs(
    usage_by_model: dict[str, UsageMetadata],
) -> list[ModelCostEstimate]:
    """모델별 usage_metadata 를 비용 추정치로 바꾼다."""

    estimates = []
    for model_name, usage in usage_by_model.items():
        cached = usage.get("input_token_details", {}).get("cache_read", 0)
        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]

        pricing = resolve_pricing(model_name)
        cost_usd = None
        if pricing is not None:
            billable_input = max(input_tokens - cached, 0)
            cost_usd = (
                billable_input * pricing.input
                + cached * pricing.cached_input
                + output_tokens * pricing.output
            ) / 1_000_000

        estimates.append(
            ModelCostEstimate(
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached,
                cost_usd=cost_usd,
            )
        )
    return sorted(estimates, key=lambda e: e.model_name)


def format_usage_summary(usage_by_model: dict[str, UsageMetadata]) -> str:
    """콘솔에 바로 찍을 수 있는 모델별 토큰·비용 요약 문자열."""

    if not usage_by_model:
        return "토큰 사용량 요약: 호출 기록 없음"

    estimates = estimate_costs(usage_by_model)
    lines = ["토큰 사용량 요약 (파이프라인 실행 기준, RAGAS 심판 호출 제외)"]
    total_cost = 0.0
    total_cost_known = True
    for e in estimates:
        cost_text = f"${e.cost_usd:.4f}" if e.cost_usd is not None else "단가 미상"
        cached_text = f", 캐시입력 {e.cached_input_tokens:,}" if e.cached_input_tokens else ""
        lines.append(
            f"  - {e.model_name}: 입력 {e.input_tokens:,} / 출력 {e.output_tokens:,}"
            f"{cached_text} → {cost_text}"
        )
        if e.cost_usd is not None:
            total_cost += e.cost_usd
        else:
            total_cost_known = False

    total_text = f"${total_cost:.4f}" + (" (일부 모델 단가 미상, 과소 추정)" if not total_cost_known else "")
    lines.append(f"  합계: {total_text}")
    return "\n".join(lines)


__all__ = [
    "ModelCostEstimate",
    "ModelPricing",
    "MODEL_PRICING_PER_1M",
    "estimate_costs",
    "format_usage_summary",
    "resolve_pricing",
]
