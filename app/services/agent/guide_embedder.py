"""대응 가이드 임베딩 모델의 인터페이스와 OpenAI 구현이다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from app.data.model.document_chunk import EMBEDDING_DIM


class GuideEmbeddingError(ValueError):
    """임베딩 응답 개수 또는 차원이 계약과 다른 경우이다."""


class GuideEmbedder(Protocol):
    """실제 모델과 테스트 Fake가 공유하는 최소 임베딩 계약이다."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class OpenAIGuideEmbedder:
    """text-embedding-3-small을 이용해 1536차원 벡터를 생성한다."""

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = EMBEDDING_DIM,
        request_timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._dimensions = dimensions
        self._embedder = OpenAIEmbeddings(
            model=model,
            dimensions=dimensions,
            request_timeout=request_timeout_seconds,
            max_retries=max_retries,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        normalized = list(texts)
        vectors = self._embedder.embed_documents(normalized)
        return validate_document_embeddings(
            normalized,
            vectors,
            dimensions=self._dimensions,
        )

    def embed_query(self, query: str) -> list[float]:
        vector = self._embedder.embed_query(query)
        return validate_embedding(vector, dimensions=self._dimensions)


def validate_document_embeddings(
    texts: Sequence[str],
    vectors: Sequence[Sequence[float]],
    *,
    dimensions: int = EMBEDDING_DIM,
) -> list[list[float]]:
    """Batch 입력 개수와 각 벡터 차원을 검증한다."""

    if len(texts) != len(vectors):
        raise GuideEmbeddingError(
            "임베딩 입력 문서 수와 결과 벡터 수가 일치하지 않는다."
        )
    return [validate_embedding(vector, dimensions=dimensions) for vector in vectors]


def validate_embedding(
    vector: Sequence[float],
    *,
    dimensions: int = EMBEDDING_DIM,
) -> list[float]:
    """DB Vector 컬럼과 동일한 차원의 숫자 목록으로 정규화한다."""

    if len(vector) != dimensions:
        raise GuideEmbeddingError(
            f"임베딩은 {dimensions}차원이어야 한다: {len(vector)}"
        )
    return [float(value) for value in vector]


__all__ = [
    "GuideEmbedder",
    "GuideEmbeddingError",
    "OpenAIGuideEmbedder",
    "validate_document_embeddings",
    "validate_embedding",
]
