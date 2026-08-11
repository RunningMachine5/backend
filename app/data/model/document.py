from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, JSON_COLUMN



class Document(SQLModel, table=True):
    """RAG 원본 문서와 검색 메타데이터를 저장한다."""

    __tablename__ = "documents"

    # 기본값이 None 인 이유: DB 가 생성한 값으로 들어가므로
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )

    # 재적재해도 같은 문서를 가리키는 업무 키
    document_key: str = Field(max_length=255, unique=True)

    # 원본 파일명
    title: str = Field(max_length=255)

    # 원본 출처
    source: str | None = Field(default=None, max_length=512)
    source_type: str | None = Field(default=None, max_length=64)
    fraud_types: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JSON_COLUMN,
            nullable=False,
        ),
    )

    audiences: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JSON_COLUMN,
            nullable=False,
        ),
    )
    # 원본 전체 텍스트
    content: str = Field(sa_column=Column(Text, nullable=False))

    # ERD의 metadata 컬럼. SQLModel/SQLAlchemy 선언 API에서 `metadata`는 예약
    # 속성이라 파이썬 속성명은 meta로 두고 DB 컬럼명만 metadata로 맞춘다.
    meta: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON_COLUMN, nullable=False),
    )

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["Document"]
