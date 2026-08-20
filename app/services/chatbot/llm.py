"""
챗봇 LLM 호출부가 공유하는 구조화 출력 클라이언트 생성기.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import CHAT_LLM_MODEL, CHAT_LLM_REASONING_EFFORT


def build_structured_llm(
    schema: type[BaseModel],
    *,
    model: str | None = None,
    timeout_seconds: float,
    reasoning_effort: str | None = CHAT_LLM_REASONING_EFFORT,
) -> Any:
    """스키마를 강제하는 구조화 출력 LLM 클라이언트를 만든다."""

    resolved_model = model or CHAT_LLM_MODEL

    # gpt-4 계열은 추론 모델이 아니라 reasoning_effort 자체를 모르는 인자라며 400을 낸다.
    # 빈 값일 때와 마찬가지로 아예 넘기지 않는다.
    if resolved_model.startswith("gpt-4"):
        reasoning_effort = None

    reasoning_options = (
        {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    )
    return ChatOpenAI(
        model=resolved_model,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=timeout_seconds,
        # 호출 횟수는 서비스에서 직접 관리해 상한을 정확히 지킨다.
        max_retries=0,
        **reasoning_options,
    ).with_structured_output(
        schema,
        method="json_schema",
        strict=True,
    )


__all__ = ["build_structured_llm"]
