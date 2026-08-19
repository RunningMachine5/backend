"""RAGAS 채점에 쓰는 심판 LLM 과 claim 분해 프롬프트를 한 곳에서 만든다."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.config import RAGAS_JUDGE_MODEL, RAGAS_JUDGE_TEMPERATURE

if TYPE_CHECKING:
    from ragas.metrics._factual_correctness import ClaimDecompositionPrompt

# ragas ClaimDecompositionPrompt 의 기본 instruction 과 few-shot 예시가 전부 영어라,
# 한국어 문장을 넣어도 심판 LLM 이 claim 을 영어로 분해할 때가 있다(사례당 최대 절반
# 가까이). response 는 한국어, reference 는 그대로거나 그 반대로 섞이면 NLI 가 서로
# 다른 언어를 대조하는 교차언어 판정이 되어 점수가 흔들린다. 예시 언어를 바꾸지 않고
# instruction 한 줄로 "입력 언어를 유지하라"고 못박아 분해 언어를 입력에 고정한다.
_LANGUAGE_INSTRUCTION = (
    "\nWrite every claim in the same language as the input text. Never translate "
    "any part of the input into another language, even if the examples above are "
    "in a different language."
)


def build_judge_llm() -> Any:
    """RAGAS 가 요구하는 BaseRagasLLM 래퍼로 심판 LLM 을 만든다
    """

    import httpx
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    judge_kwargs: dict[str, Any] = {
        "model": RAGAS_JUDGE_MODEL,
        "http_async_client": httpx.AsyncClient(),
    }
    if RAGAS_JUDGE_TEMPERATURE:
        judge_kwargs["temperature"] = float(RAGAS_JUDGE_TEMPERATURE)
    return LangchainLLMWrapper(ChatOpenAI(**judge_kwargs), bypass_temperature=True)


def build_claim_decomposition_prompt(
    *, atomicity: str = "low", coverage: str = "low"
) -> "ClaimDecompositionPrompt":
    """FactualCorrectness 와 같은 atomicity/coverage 예시를 끼운 분해 프롬프트.

    ragas 는 eval 전용 의존성이라 함수 안에서 import 한다.
    """

    from ragas.metrics._factual_correctness import (
        ClaimDecompositionPrompt,
        DecompositionType,
        claim_decomposition_examples,
    )

    prompt = ClaimDecompositionPrompt()
    wanted = f"{atomicity}_atomicity_{coverage}_coverage"
    prompt.examples = [
        example
        for item in DecompositionType
        if item.value == wanted
        for example in claim_decomposition_examples[item]
    ]
    prompt.instruction += _LANGUAGE_INSTRUCTION
    return prompt


__all__ = ["build_claim_decomposition_prompt", "build_judge_llm"]
