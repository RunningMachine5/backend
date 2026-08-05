from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

# fake_embedder 등 현재 서비스에서 쓰는 임베딩 모델 차원과 맞춘다.
EMBEDDING_DIM = 1536

class DocumentChunk(SQLModel, table=True):
    """문서 청크 + 임베딩 값 저장 테이블 (Document와 1 : N)"""

    __tablename__ = "document_chunks"

    id: int | None = Field(default=None, primary_key=True)

    document_id: int = Field(foreign_key="documents.id", index=True, nullable=False)

    # 원본 문서 내에서의 청크 순서 (0부터 시작)
    chunk_index: int = Field(nullable=False)

    # 잘린 청크 원문
    # sa_column: SQLModel이 지원하지 않는/추론 못하는 타입일 때 SQLAlchemy 한테 짬때리기
    content: str = Field(sa_column=Column(Text, nullable=False))
    # 임베딩된 잘린 청크
    embedding: list[float] = Field(sa_column=Column(Vector(EMBEDDING_DIM), nullable=False))

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
