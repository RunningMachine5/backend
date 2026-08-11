"""챗봇 RAG 고객대응가이드 전용 청크·임베딩 테이블."""

from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Index, Integer, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY

# docs_embedding이 쓰는 OpenAI text-embedding-3-small 차원과 맞춘다.
CS_GUIDE_EMBEDDING_DIM = 1536


class CsGuideDocumentChunk(SQLModel, table=True):
    """고객대응가이드 청크 + 임베딩 값 저장 테이블 (CsGuideDocument와 1 : N)"""

    __tablename__ = "cs_guide_document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "cs_guide_document_id",
            "chunk_index",
            name="uq_cs_guide_document_chunks_document_id_chunk_index",
        ),
        # 인덱스가 없으면 RAG 질의마다 전체 청크 거리 계산(seq scan)이 일어난다.
        # 방언 접두사가 붙은 옵션이라 SQLite에서는 평범한 인덱스로 무시된다.
        Index(
            "ix_cs_guide_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )

    cs_guide_document_id: int = Field(
        foreign_key="cs_guide_documents.id",
        ondelete="CASCADE",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )

    # 원본 문서 내에서의 청크 순서 (0부터 시작). 페이지 번호가 아니다.
    # (cs_guide_document_id, chunk_index)가 UNIQUE라 한 페이지에서 여러 청크가
    # 나와도 충돌하지 않도록 문서 전체에서 단조 증가하는 값을 넣어야 한다.
    chunk_index: int = Field(nullable=False)

    # 이 청크가 나온 원본 페이지 번호 (1부터 시작).
    # 한 페이지가 여러 청크로 쪼개지면 같은 page 값이 여러 행에 반복되므로
    # chunk_index로 대체할 수 없다. 페이지 개념이 없는 원본은 NULL.
    page: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))

    # 잘린 청크 원문
    # sa_column: SQLModel이 지원하지 않는/추론 못하는 타입일 때 SQLAlchemy 한테 짬때리기
    content: str = Field(sa_column=Column(Text, nullable=False))

    # 임베딩된 잘린 청크
    embedding: list[float] = Field(
        sa_column=Column(Vector(CS_GUIDE_EMBEDDING_DIM), nullable=False)
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["CS_GUIDE_EMBEDDING_DIM", "CsGuideDocumentChunk"]
