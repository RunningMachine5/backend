"""
챗봇 전용 리트리버

MAX_DISTANCE 를 놓아 관련없는 청크를 억지로 가져오는 경우를 방지
상위 객체로부터 DB 세션을 주입받아 사용해야함
각 청크를 어디서 가져왔는지 추적할 수 있도록 
"""
from sqlmodel import Session, select
from app.data.model.document import Document
from app.data.model.document_chunk import DocumentChunk
from app.services.rag.docs_embedding import query_embedding

MAX_DISTANCE = 0.6  # 코사인 거리 이보다 멀면 관련 없는 청크로 본다

"""
리트리버
"""
def retriever(question: str, session: Session, top_k: int = 3) -> str:
    """질문을 임베딩해서 document_chunks 에서 유사한 청크를 찾아 context 로 합친다"""
    question_vector = query_embedding(question)
    distance = DocumentChunk.embedding.cosine_distance(question_vector)

    stmt = (
        select(DocumentChunk.content, distance.label("distance"))
        .order_by(distance)
        .limit(top_k)
    )
    rows = session.exec(stmt).all()
    contents = [content for content, dist in rows if dist <= MAX_DISTANCE]
    if not contents:
        return "관련 문서를 찾지 못했습니다."

    return "\n\n".join(contents)

"""
리트리버인데 출처 넣는 기능이 추가됨
"""
def retriever_source(question: str, session: Session, top_k: int = 3) -> str:
    """질문을 임베딩해서 document_chunks 에서 유사한 청크를 찾아 context 로 합친다"""
    question_vector = query_embedding(question)
    distance = DocumentChunk.embedding.cosine_distance(question_vector)

    stmt = (
        select(Document.title, DocumentChunk.chunk_index, DocumentChunk.content, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(distance)
        .limit(top_k)
    )
    rows = [r for r in session.exec(stmt).all() if r.distance <= MAX_DISTANCE]
    if not rows:
        return "관련 문서를 찾지 못했습니다."

    # 모델이 "어느 문서 몇 페이지"를 인용할 수 있도록 실제 출처를 붙인다
    # 제목이 너무 길어지면 컨텍스트를 망칠 수 있으므로 앞 20개만 잘라서 제목으로 넣는다 (개발자만 식별하면되니까)
    return "\n\n".join(
        f"[출처: {r.title[:20]} {r.chunk_index}p]\n{r.content}" for r in rows
    )