"""Agent 대응 가이드 적재와 검색에서 사용하는 DTO이다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuideIndexResultDTO:
    """Markdown 코퍼스를 DB에 적재한 결과이다."""

    document_count: int
    chunk_count: int
    embedded_chunk_count: int
    created_document_count: int
    updated_document_count: int
    unchanged_document_count: int


@dataclass(frozen=True, slots=True)
class GuideSearchRequestDTO:
    """대응 가이드 검색어와 메타데이터 필터이다."""

    query: str
    fraud_type: str | None = None
    audience: str | None = None
    risk_grade: str | None = None
    action_codes: tuple[str, ...] = ()
    top_k: int = 5


@dataclass(frozen=True, slots=True)
class RetrievedGuideChunkDTO:
    """벡터 검색으로 조회한 하나의 대응 가이드 Chunk이다."""

    document_id: str
    title: str
    chunk_index: int
    heading: str
    content: str
    source_type: str
    source_name: str
    source_url: str | None
    similarity_score: float
    retrieval_rank: int


__all__ = [
    "GuideIndexResultDTO",
    "GuideSearchRequestDTO",
    "RetrievedGuideChunkDTO",
]
