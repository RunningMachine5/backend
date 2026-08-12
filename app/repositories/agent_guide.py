"""Agent 대응 가이드 적재와 벡터 검색을 담당하는 Repository이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import cast, delete, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session, select

from app.data.model.document import Document
from app.data.model.document_chunk import DocumentChunk


@dataclass(frozen=True, slots=True)
class GuideSearchRow:
    """DB 검색 결과를 서비스 계층으로 전달하는 내부 조회 모델이다."""

    document: Document
    chunk: DocumentChunk
    similarity_score: float


class AgentGuideRepository:
    """문서 Upsert와 Chunk 교체를 하나의 트랜잭션 안에서 수행한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_document(self, document_key: str) -> Document | None:
        return self.session.exec(
            select(Document).where(Document.document_key == document_key)
        ).first()

    def add(self, document: Document) -> None:
        self.session.add(document)

    def flush(self) -> None:
        self.session.flush()

    def replace_chunks(
        self,
        document_id: int,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        """문서의 이전 버전 Chunk를 제거하고 새 Chunk로 완전히 교체한다."""

        self.session.exec(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        self.session.add_all(list(chunks))

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def search_chunks(
        self,
        query_embedding: Sequence[float],
        *,
        fraud_type: str | None,
        audience: str | None,
        risk_grade: str | None,
        action_codes: Sequence[str],
        top_k: int,
        apply_metadata_filter: bool,
    ) -> list[GuideSearchRow]:
        """PostgreSQL에서 JSONB 필터와 pgvector 코사인 검색을 수행한다."""

        distance = DocumentChunk.embedding.cosine_distance(list(query_embedding))
        statement = (
            select(Document, DocumentChunk, distance.label("distance"))
            .join(Document, Document.id == DocumentChunk.document_id)
        )

        if apply_metadata_filter:
            if fraud_type:
                statement = statement.where(
                    cast(Document.fraud_types, JSONB).contains([fraud_type])
                )
            if audience:
                statement = statement.where(
                    or_(
                        cast(Document.audiences, JSONB).contains([audience]),
                        cast(Document.audiences, JSONB).contains(["COMMON"]),
                    )
                )
            if risk_grade:
                statement = statement.where(
                    cast(Document.meta["risk_grades"], JSONB).contains([risk_grade])
                )
            if action_codes:
                statement = statement.where(
                    or_(
                        *(
                            cast(Document.meta["action_codes"], JSONB).contains([code])
                            for code in action_codes
                        )
                    )
                )
        rows = self.session.exec(
            statement.order_by(distance).limit(top_k)
        ).all()
        return [
            GuideSearchRow(
                document=document,
                chunk=chunk,
                similarity_score=1.0 - float(row_distance),
            )
            for document, chunk, row_distance in rows
        ]


__all__ = ["AgentGuideRepository", "GuideSearchRow"]
