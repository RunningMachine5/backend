from app.data.fake_data import MONITORING_GUIDE_TEXT
from app.dto.agent import RagQueryDTO, RetrievedContextDTO


class FakeVectorDB:
    """실제 VectorDB 대신 항상 같은 모니터링 문맥을 반환한다."""

    def retrieve(self, query: RagQueryDTO) -> RetrievedContextDTO:
        """질의 DTO를 검색 경계로 받고 고정된 유사 사례 문서를 반환한다."""
        # Fake 구현에서는 질의 내용과 무관하게 동일한 문서를 반환한다.
        _ = query
        return RetrievedContextDTO(
            title="고액 이상 카드결제 모니터링 가이드",
            content=MONITORING_GUIDE_TEXT,
            source="금융보안원 이상금융거래 모니터링 가이드(Fake)",
            similarity_score=0.92,
        )

