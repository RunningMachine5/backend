from app.data.fake_data import CUSTOMER_GUIDE_TEXT
from app.dto.chatbot import CustomerGuideDTO


class FakeGuideRetriever:
    """고객 대응 가이드 VectorDB를 대신해 항상 같은 문서를 반환한다."""

    def retrieve(self, embedding: list[float]) -> CustomerGuideDTO:
        """질문 임베딩을 검색 경계로 받고 고정된 고객 대응 문서를 반환한다."""
        _ = embedding
        return CustomerGuideDTO(
            title="미확인 카드거래 고객 대응 가이드",
            content=CUSTOMER_GUIDE_TEXT,
        )

