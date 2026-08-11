"""챗봇 RAG 고객대응가이드 전용 원본 문서 테이블."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class CsGuideDocument(SQLModel, table=True):
    """고객대응가이드 원본 문서 정보 테이블.

    documents와 달리 채널 구분(audience)이나 재적재 업무 키(document_key)가
    없다. 고객 챗봇 RAG 한 곳에서만 쓰고, 다시 적재할 때는 기존 행을 지우고
    새로 넣는다는 전제다.
    """

    __tablename__ = "cs_guide_documents"

    # 기본값이 None 인 이유: DB 가 생성한 값으로 들어가므로
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )

    # 원본 파일명
    title: str = Field(max_length=255)

    # 원본 출처
    source: str | None = Field(default=None, max_length=512)
    source_type: str | None = Field(default=None, max_length=64)
    fraud_type: str | None = Field(default=None, max_length=64)

    # 원본 전체 텍스트
    content: str = Field(sa_column=Column(Text, nullable=False))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["CsGuideDocument"]
