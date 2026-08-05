from datetime import datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class Document(SQLModel, table=True):
    """RAG 원본 문서 정보 테이블"""

    __tablename__ = "documents"

    # 기본값이 None 인 이유: DB 가 생성한 값으로 들어가지므로
    id: int | None = Field(default=None, primary_key=True)

    # 원본 파일명
    title: str = Field(max_length=255)

    # 원본 출처
    source: str | None = Field(default=None, max_length=512)

    # 원본 전체 텍스트
    content: str = Field(sa_column=Column(Text, nullable=False))

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.now, nullable=False)
