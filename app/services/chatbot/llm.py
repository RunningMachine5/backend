"""챗봇 LLM 호출부가 공유하는 구조화 출력 클라이언트 생성기.

평가·추출·대응 가이드 생성이 같은 모델 설정과 재시도 정책을 쓰도록 한곳에 둔다.
타임아웃과 재시도 상한은 app/core/config.py 의 CHAT_LLM_* env var 가 출처다.
모델도 마찬가지로 CHAT_LLM_MODEL 이 기본값이며, 대응 가이드 생성처럼 상위 모델이
필요한 호출부만 model 인자로 CHAT_RESPONSE_LLM_MODEL 을 넘긴다.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import CHAT_LLM_MODEL


def build_structured_llm(
    schema: type[BaseModel],
    *,
    model: str | None = None,
    timeout_seconds: float,
) -> Any:
    """스키마를 강제하는 구조화 출력 LLM 클라이언트를 만든다."""

    return ChatOpenAI(
        model=model or CHAT_LLM_MODEL,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=timeout_seconds,
        # 호출 횟수는 서비스에서 직접 관리해 상한을 정확히 지킨다.
        max_retries=0,
    ).with_structured_output(
        schema,
        method="json_schema",
        strict=True,
    )


__all__ = ["build_structured_llm"]
