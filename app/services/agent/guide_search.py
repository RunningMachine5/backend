"""Agent 대응 계획에 사용할 문서 Chunk를 검색한다."""

from __future__ import annotations

from app.dto.agent_guide import GuideSearchRequestDTO, RetrievedGuideChunkDTO
from app.repositories.agent_guide import AgentGuideRepository, GuideSearchRow
from app.services.agent.guide_embedder import GuideEmbedder, validate_embedding


class GuideSearchService:
    """메타데이터 선필터와 의미 유사도 검색을 결합한다."""

    def __init__(
        self,
        repository: AgentGuideRepository,
        embedder: GuideEmbedder,
    ) -> None:
        self.repository = repository
        self.embedder = embedder

    def search(
        self,
        request: GuideSearchRequestDTO,
        *,
        apply_metadata_filter: bool = True,
    ) -> list[RetrievedGuideChunkDTO]:
        query_embedding = validate_embedding(self.embedder.embed_query(request.query))
        return self.search_with_embedding(
            request,
            query_embedding,
            apply_metadata_filter=apply_metadata_filter,
        )

    def compare(
        self,
        request: GuideSearchRequestDTO,
    ) -> tuple[list[RetrievedGuideChunkDTO], list[RetrievedGuideChunkDTO]]:
        """같은 Query 임베딩으로 기준 검색과 개선 검색을 공정하게 비교한다."""

        query_embedding = validate_embedding(self.embedder.embed_query(request.query))
        baseline = self.search_with_embedding(
            request,
            query_embedding,
            apply_metadata_filter=False,
        )
        filtered = self.search_with_embedding(
            request,
            query_embedding,
            apply_metadata_filter=True,
        )
        return baseline, filtered

    def search_with_embedding(
        self,
        request: GuideSearchRequestDTO,
        query_embedding: list[float],
        *,
        apply_metadata_filter: bool,
    ) -> list[RetrievedGuideChunkDTO]:
        if not request.query.strip():
            raise ValueError("대응 가이드 검색어는 비어 있을 수 없다.")
        if request.top_k < 1:
            raise ValueError("top_k는 1 이상이어야 한다.")

        rows = self.repository.search_chunks(
            query_embedding,
            fraud_type=request.fraud_type,
            audience=request.audience,
            risk_grade=request.risk_grade,
            action_codes=request.action_codes,
            top_k=request.top_k,
            apply_metadata_filter=apply_metadata_filter,
        )
        return [_to_dto(row, rank) for rank, row in enumerate(rows, start=1)]


def _to_dto(row: GuideSearchRow, rank: int) -> RetrievedGuideChunkDTO:
    document_meta = row.document.meta or {}
    chunk_meta = row.chunk.meta or {}
    return RetrievedGuideChunkDTO(
        document_id=row.document.document_key,
        title=row.document.title,
        chunk_index=row.chunk.chunk_index,
        heading=str(chunk_meta.get("heading", row.document.title)),
        content=row.chunk.content,
        source_type=str(row.document.source_type or ""),
        source_name=str(document_meta.get("source_name", "")),
        source_url=document_meta.get("source_url"),
        similarity_score=round(row.similarity_score, 6),
        retrieval_rank=rank,
    )


__all__ = ["GuideSearchService"]
