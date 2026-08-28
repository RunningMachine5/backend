"""Cohere API로 고객 대응 가이드 검색 후보를 재정렬한다."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import Any

import cohere
import httpx

from app.core.config import (
    COHERE_API_KEY,
    COHERE_RERANK_MAX_RETRIES,
    COHERE_RERANK_MODEL,
    COHERE_RERANK_TIMEOUT_SECONDS,
)
from app.dto.chatbot import RetrievedChatbotGuideChunkDTO


class CohereRerankError(RuntimeError):
    """Cohere 호출 또는 응답 계약 검증에 실패했다."""


class CohereReranker:
    """Cohere의 다국어 리랭커로 벡터 검색 후보를 재정렬한다."""

    def __init__(
        self,
        *,
        api_key: str = COHERE_API_KEY,
        model: str = COHERE_RERANK_MODEL,
        timeout_seconds: float = COHERE_RERANK_TIMEOUT_SECONDS,
        max_retries: int = COHERE_RERANK_MAX_RETRIES,
        client: Any | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = max(0.1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self._client = client
        self.before_request = before_request

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChatbotGuideChunkDTO],
        *,
        top_k: int,
    ) -> list[RetrievedChatbotGuideChunkDTO]:
        """질의와 청크 본문만 보내고 Cohere가 반환한 순서로 후보를 고른다."""

        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다")
        if not candidates:
            return []

        expected_count = min(top_k, len(candidates))
        response = self._request_rerank(
            query=query,
            documents=[candidate.content for candidate in candidates],
            top_n=expected_count,
        )

        results = getattr(response, "results", None)
        if not isinstance(results, list) or len(results) != expected_count:
            raise CohereRerankError("Cohere 리랭커 결과 개수가 올바르지 않습니다")

        ordered = []
        seen_indices: set[int] = set()
        for result in results:
            index = getattr(result, "index", None)
            relevance_score = getattr(result, "relevance_score", None)
            if isinstance(index, bool) or not isinstance(index, int):
                raise CohereRerankError("Cohere 리랭커 결과 인덱스가 정수가 아닙니다")
            if index < 0 or index >= len(candidates) or index in seen_indices:
                raise CohereRerankError("Cohere 리랭커 결과 인덱스가 올바르지 않습니다")
            if (
                isinstance(relevance_score, bool)
                or not isinstance(relevance_score, (int, float))
                or not math.isfinite(float(relevance_score))
            ):
                raise CohereRerankError("Cohere 리랭커 관련도 점수가 올바르지 않습니다")
            seen_indices.add(index)
            ordered.append(candidates[index])
        return ordered

    def _request_rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> Any:
        client = self._get_client()
        for attempt in range(self.max_retries + 1):
            try:
                if self.before_request is not None:
                    self.before_request()
                return client.rerank(
                    model=self.model,
                    query=query,
                    documents=documents,
                    top_n=top_n,
                )
            except Exception as exc:
                if attempt >= self.max_retries or not _is_retryable(exc):
                    status_code = getattr(exc, "status_code", None)
                    detail = type(exc).__name__
                    if status_code is not None:
                        detail = f"{detail}, status={status_code}"
                    raise CohereRerankError(
                        f"Cohere 리랭커 호출에 실패했습니다 ({detail})"
                    ) from exc
                time.sleep(min(0.25 * (2**attempt), 1.0))
        raise AssertionError("도달할 수 없는 Cohere 재시도 상태입니다")

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise CohereRerankError("COHERE_API_KEY가 설정되지 않았습니다")
        try:
            self._client = cohere.ClientV2(
                api_key=self.api_key,
                timeout=self.timeout_seconds,
                client_name="fdshield-backend",
            )
        except Exception as exc:
            raise CohereRerankError("Cohere 클라이언트를 만들지 못했습니다") from exc
        return self._client


def _is_retryable(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return (
        isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError))
        or status_code == 429
        or isinstance(status_code, int)
        and 500 <= status_code < 600
    )


__all__ = ["CohereRerankError", "CohereReranker"]
