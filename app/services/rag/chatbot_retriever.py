"""거리 상한을 적용해 고객 대응 가이드 청크를 검색한다."""
from sqlmodel import Session, select

from app.data.model.cs_guide_document import CsGuideDocument
from app.data.model.cs_guide_document_chunk import CsGuideDocumentChunk
from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.docs_embedding import query_embedding

MAX_DISTANCE = 0.6

def retriever_source(
    question: str,
    session: Session,
    top_k: int = 3,
) -> list[RetrievedChatbotGuideChunkDTO]:
    """질문과 가까운 고객 대응 가이드 청크를 출처 정보와 함께 반환한다."""

    question_vector = query_embedding(question)
    distance = CsGuideDocumentChunk.embedding.cosine_distance(question_vector)

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
        .limit(top_k)
    )
    rows = session.exec(stmt).all()
    return [
        RetrievedChatbotGuideChunkDTO(
            content=content,
            source_title=title,
            page=page,
            distance=float(chunk_distance),
        )
        for title, page, content, chunk_distance in rows
    ]
