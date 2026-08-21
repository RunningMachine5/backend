"""챗봇 RAG 고객대응가이드 전용 원본 문서 테이블."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class CsGuideDocument(SQLModel, table=True):
    """고객 대응 가이드 원본 문서."""

    __tablename__ = "cs_guide_documents"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )

    title: str = Field(max_length=255)

    source: str | None = Field(default=None, max_length=512)
    source_type: str | None = Field(default=None, max_length=64)
    fraud_type: str | None = Field(default=None, max_length=64)

    content: str = Field(sa_column=Column(Text, nullable=False))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["CsGuideDocument"]
