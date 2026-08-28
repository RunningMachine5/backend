"""DB나 OpenAI 없이 Cohere 리랭커 API 연결만 확인한다."""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()

from app.core.config import (  # noqa: E402
    COHERE_API_KEY,
    COHERE_RERANK_ENABLED,
    COHERE_RERANK_MODEL,
    COHERE_RERANK_TIMEOUT_SECONDS,
)
from app.services.rag.cohere_diagnostics import diagnose_cohere_reranker  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not COHERE_RERANK_ENABLED:
        # 연결 확인 자체는 그대로 진행한다. 다만 이 상태로는 챗봇 검색 경로가
        # 리랭커를 호출하지 않으므로 결과를 오해하지 않도록 먼저 알린다.
        logging.warning(
            "COHERE_RERANK_ENABLED=false 입니다. "
            "연결은 확인하지만 챗봇 검색은 리랭커를 호출하지 않습니다"
        )
    succeeded = diagnose_cohere_reranker(
        api_key=COHERE_API_KEY,
        model=COHERE_RERANK_MODEL,
        timeout_seconds=COHERE_RERANK_TIMEOUT_SECONDS,
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
