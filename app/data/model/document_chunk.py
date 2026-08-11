from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, JSON_COLUMN

# fake_embedder 등 현재 서비스에서 쓰는 임베딩 모델 차원과 맞춘다.
EMBEDDING_DIM = 1536


class DocumentChunk(SQLModel, table=True):
    """문서 청크 + 임베딩 값 저장 테이블 (Document와 1 : N)"""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_id_chunk_index",
        ),
        # 인덱스가 없으면 RAG 질의마다 전체 청크 거리 계산(seq scan)이 일어난다.
        # 방언 접두사가 붙은 옵션이라 SQLite에서는 평범한 인덱스로 무시된다.
        Index(
            "ix_document_chunks_embedding_hnsw",
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

    document_id: int = Field(
        foreign_key="documents.id",
        ondelete="CASCADE",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )

    # 원본 문서 내에서의 청크 순서 (0부터 시작). 페이지 번호가 아니다.
    # (document_id, chunk_index)가 UNIQUE라 한 페이지에서 여러 청크가 나와도
    # 충돌하지 않도록 문서 전체에서 단조 증가하는 값을 넣어야 한다.
    chunk_index: int = Field(nullable=False)

    # 잘린 청크 원문
    # sa_column: SQLModel이 지원하지 않는/추론 못하는 타입일 때 SQLAlchemy 한테 짬때리기
    content: str = Field(sa_column=Column(Text, nullable=False))

    # ERD의 metadata 컬럼. `metadata`는 선언 API 예약어라 속성명만 meta로 둔다.
    meta: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON_COLUMN, nullable=False),
    )

    # 임베딩된 잘린 청크
    embedding: list[float] = Field(
        sa_column=Column(Vector(EMBEDDING_DIM), nullable=False)
    )

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["EMBEDDING_DIM", "DocumentChunk"]
