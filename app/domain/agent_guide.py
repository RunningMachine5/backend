"""Agent RAG 대응 문서와 검색 청크의 저장 형식 독립 도메인 모델이다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


class GuideCorpusError(ValueError):
    """대응 가이드 코퍼스를 처리할 때 발생하는 오류의 공통 형식이다."""


class GuideDocumentParseError(GuideCorpusError):
    """Markdown 또는 YAML Front Matter를 읽을 수 없을 때 발생한다."""


class GuideDocumentValidationError(GuideCorpusError):
    """대응 문서의 메타데이터나 본문이 계약에 맞지 않을 때 발생한다."""


class DuplicateGuideDocumentError(GuideCorpusError):
    """코퍼스 안에서 동일한 문서 ID가 두 번 발견될 때 발생한다."""


class GuideChunkingError(GuideCorpusError):
    """검증된 문서에서 검색 가능한 청크를 만들 수 없을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class GuideDocument:
    """Front Matter 검증이 끝난 하나의 RAG 원본 문서이다."""

    document_id: str
    title: str
    source_type: str
    source_name: str
    source_url: str | None
    fraud_types: tuple[str, ...]
    audiences: tuple[str, ...]
    topics: tuple[str, ...]
    published_at: date | None
    accessed_at: date
    content: str
    source_path: Path


@dataclass(frozen=True, slots=True)
class GuideChunk:
    """제목과 본문 문맥을 보존한 하나의 의미 기반 검색 단위이다."""

    document_id: str
    document_title: str
    chunk_index: int
    heading: str
    content: str
    source_type: str
    source_name: str
    source_url: str | None
    fraud_types: tuple[str, ...]
    audiences: tuple[str, ...]
    topics: tuple[str, ...]


__all__ = [
    "DuplicateGuideDocumentError",
    "GuideChunk",
    "GuideChunkingError",
    "GuideCorpusError",
    "GuideDocument",
    "GuideDocumentParseError",
    "GuideDocumentValidationError",
]
