"""
챗봇 전용 리트리버

MAX_DISTANCE 를 놓아 관련없는 청크를 억지로 가져오는 경우를 방지
상위 객체로부터 DB 세션을 주입받아 사용해야함
각 청크를 어디서 가져왔는지 추적할 수 있도록 
"""
from sqlmodel import Session, select

from app.data.model.cs_guide_document import CsGuideDocument
from app.data.model.cs_guide_document_chunk import CsGuideDocumentChunk
from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.docs_embedding import query_embedding

MAX_DISTANCE = 0.6  # 코사인 거리 이보다 멀면 관련 없는 청크로 본다

"""
리트리버
"""
def retriever(question: str, session: Session, top_k: int = 3) -> str:
    """질문을 임베딩해서 cs_guide_document_chunks 에서 유사한 청크를 찾아 context 로 합친다"""
    question_vector = query_embedding(question)
    distance = CsGuideDocumentChunk.embedding.cosine_distance(question_vector)

    stmt = (
        select(CsGuideDocumentChunk.content, distance.label("distance"))
        .order_by(distance)
        .limit(top_k)
    )
    rows = session.exec(stmt).all()
    contents = [content for content, dist in rows if dist <= MAX_DISTANCE]
    if not contents:
        return "관련 문서를 찾지 못했습니다."

    return "\n\n".join(contents)

def retriever_source(
    question: str,
    session: Session,
    top_k: int = 3,
) -> list[RetrievedChatbotGuideChunkDTO]:
    """질문과 가까운 고객 대응 가이드 청크를 출처 정보와 함께 반환한다."""

    question_vector = query_embedding(question)
    # 코사인 유사도 높은 질문 찾아오기
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
        .order_by(distance) # 코사인 유사도 순으로 정렬
        .limit(top_k) # 상위 k개만 가져온다
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
