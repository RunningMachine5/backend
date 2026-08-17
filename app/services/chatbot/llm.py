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

    # 추론 모델이 아닌 모델로 바꿔 끼울 때를 위해 빈 값이면 인자를 넘기지 않는다.
    reasoning_options = (
        {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    )
    return ChatOpenAI(
        model=model or CHAT_LLM_MODEL,
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
