"""벡터 후보 검색 후 Cohere로 고객 대응 가이드 청크를 재정렬한다."""

import logging
from functools import lru_cache

from sqlmodel import Session, select

from app.core.config import COHERE_RERANK_CANDIDATE_K, COHERE_RERANK_ENABLED
from app.data.model.cs_guide_document import CsGuideDocument
from app.data.model.cs_guide_document_chunk import CsGuideDocumentChunk
from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.docs_embedding import query_embedding
from app.services.rag.cohere_reranker import CohereRerankError, CohereReranker

MAX_DISTANCE = 0.6
logger = logging.getLogger(__name__)


@lru_cache
def _default_reranker() -> CohereReranker:
    """프로세스에서 Cohere 클라이언트를 한 번만 만들어 재사용한다."""

    return CohereReranker()


@lru_cache
def _log_rerank_disabled() -> None:
    """의도적으로 끈 설정이므로 안내는 프로세스당 한 번만 남긴다."""

    logger.info(
        "COHERE_RERANK_ENABLED=false 라 Cohere 리랭킹 없이 "
        "벡터 검색 순위를 사용합니다"
    )


def retriever_source(
    question: str,
    session: Session,
    top_k: int = 3,
    *,
    reranker: CohereReranker | None = None,
) -> list[RetrievedChatbotGuideChunkDTO]:
    """거리 상한 내 후보를 Cohere로 재정렬하고 실패하면 벡터 순위를 쓴다."""

    question_vector = query_embedding(question)
    distance = CsGuideDocumentChunk.embedding.cosine_distance(question_vector)
    candidate_k = (
        max(COHERE_RERANK_CANDIDATE_K, top_k)
        if COHERE_RERANK_ENABLED
        else top_k
    )

    stmt = (
        select(
            CsGuideDocument.title,
            CsGuideDocumentChunk.page,
            CsGuideDocumentChunk.content,
            distance,
        )
        .join(
            CsGuideDocument,
            CsGuideDocument.id == CsGuideDocumentChunk.cs_guide_document_id,
        )
        .where(distance <= MAX_DISTANCE)
        .order_by(distance)
        .limit(candidate_k)
    )
    rows = session.exec(stmt).all()
    candidates = [
        RetrievedChatbotGuideChunkDTO(
            content=content,
            source_title=title,
            page=page,
            distance=float(chunk_distance),
        )
        for title, page, content, chunk_distance in rows
    ]
    if not COHERE_RERANK_ENABLED:
        _log_rerank_disabled()
        return candidates[:top_k]
    if not candidates:
        return candidates[:top_k]

    try:
        return (reranker or _default_reranker()).rerank(
            question,
            candidates,
            top_k=top_k,
        )
    except CohereRerankError as exc:
        logger.warning(
            "Cohere 리랭킹 실패로 벡터 검색 순위를 사용합니다: error=%s",
            exc,
        )
        return candidates[:top_k]


__all__ = ["MAX_DISTANCE", "retriever_source"]
