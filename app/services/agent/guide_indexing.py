"""검증된 Markdown 대응 가이드를 임베딩하여 DB에 적재한다."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from app.data.model.document import Document
from app.data.model.document_chunk import DocumentChunk
from app.domain.agent_guide import GuideChunk, GuideDocument
from app.dto.agent_guide import GuideIndexResultDTO
from app.repositories.agent_guide import AgentGuideRepository
from app.services.agent.guide_corpus import (
    DEFAULT_CORPUS_ROOT,
    create_guide_chunks,
    load_guide_corpus,
)
from app.services.agent.guide_embedder import GuideEmbedder


class GuideIndexingService:
    """변경된 문서만 다시 임베딩하고 문서별 Chunk를 원자적으로 교체한다."""

    def __init__(
        self,
        repository: AgentGuideRepository,
        embedder: GuideEmbedder,
    ) -> None:
        self.repository = repository
        self.embedder = embedder

    def index_corpus(
        self,
        corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    ) -> GuideIndexResultDTO:
        return self.index_documents(load_guide_corpus(corpus_root))

    def index_documents(
        self,
        documents: Sequence[GuideDocument],
    ) -> GuideIndexResultDTO:
        prepared = [
            (document, create_guide_chunks(document)) for document in documents
        ]
        changed: list[tuple[GuideDocument, tuple[GuideChunk, ...]]] = []
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for document, chunks in prepared:
            existing = self.repository.get_document(document.document_id)
            previous_hash = (existing.meta or {}).get("content_hash") if existing else None
            if previous_hash == _create_document_hash(document):
                unchanged_count += 1
                continue
            changed.append((document, chunks))
            if existing is None:
                created_count += 1
            else:
                updated_count += 1

        all_changed_chunks = [chunk for _, chunks in changed for chunk in chunks]
        try:
            embeddings = (
                self.embedder.embed_documents(
                    [chunk.content for chunk in all_changed_chunks]
                )
                if all_changed_chunks
                else []
            )
            embedding_index = 0
            for document, chunks in changed:
                target = self.repository.get_document(document.document_id) or Document(
                    document_key=document.document_id,
                    title=document.title,
                    content=document.content,
                )
                _apply_document(target, document)
                self.repository.add(target)
                self.repository.flush()
                if target.id is None:
                    raise RuntimeError("대응 가이드 document_id가 생성되지 않았다.")

                chunk_models = []
                for chunk in chunks:
                    chunk_models.append(
                        _to_chunk_model(
                            chunk,
                            document_id=target.id,
                            embedding=embeddings[embedding_index],
                        )
                    )
                    embedding_index += 1
                self.repository.replace_chunks(target.id, chunk_models)
            self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise

        return GuideIndexResultDTO(
            document_count=len(prepared),
            chunk_count=sum(len(chunks) for _, chunks in prepared),
            embedded_chunk_count=len(all_changed_chunks),
            created_document_count=created_count,
            updated_document_count=updated_count,
            unchanged_document_count=unchanged_count,
        )


def _create_document_hash(document: GuideDocument) -> str:
    """변경된 문서만 다시 임베딩할 수 있도록 문서 지문을 생성한다."""

    values = (
        document.title,
        document.content,
        document.source_type,
        document.source_name,
        document.source_url or "",
        *document.fraud_types,
        *document.audiences,
        *document.topics,
        *document.risk_grades,
        *document.action_codes,
        document.version,
    )
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _apply_document(target: Document, source: GuideDocument) -> None:
    """검증된 Markdown 문서와 검색 메타데이터를 DB 문서에 반영한다."""

    target.document_key = source.document_id
    target.title = source.title
    target.source = source.source_url
    target.source_type = source.source_type
    target.fraud_types = list(source.fraud_types)
    target.audiences = list(source.audiences)
    target.content = source.content
    target.meta = {
        "source_name": source.source_name,
        "source_url": source.source_url,
        "topics": list(source.topics),
        "risk_grades": list(source.risk_grades),
        "action_codes": list(source.action_codes),
        "version": source.version,
        "published_at": (
            source.published_at.isoformat()
            if source.published_at is not None
            else None
        ),
        "accessed_at": source.accessed_at.isoformat(),
        "source_path": source.source_path.as_posix(),
        "content_hash": _create_document_hash(source),
    }
    target.updated_at = datetime.now()


def _to_chunk_model(
    chunk: GuideChunk,
    *,
    document_id: int,
    embedding: list[float],
) -> DocumentChunk:
    """검색 Chunk의 본문·메타데이터·임베딩을 하나의 DB 행으로 만든다."""

    return DocumentChunk(
        document_id=document_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        meta={
            "document_key": chunk.document_id,
            "heading": chunk.heading,
            "source_type": chunk.source_type,
            "source_name": chunk.source_name,
            "source_url": chunk.source_url,
            "fraud_types": list(chunk.fraud_types),
            "audiences": list(chunk.audiences),
            "topics": list(chunk.topics),
            "risk_grades": list(chunk.risk_grades),
            "action_codes": list(chunk.action_codes),
            "version": chunk.version,
        },
        embedding=embedding,
    )


__all__ = ["GuideIndexingService"]
