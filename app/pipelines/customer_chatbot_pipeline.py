from app.dto.chatbot import ChatbotRequestDTO, ChatbotResponseDTO
from app.services.chatbot.fake_embedder import FakeEmbedder
from app.services.chatbot.fake_guide_retriever import FakeGuideRetriever
from app.services.chatbot.fake_transaction_repository import (
    FakeTransactionRepository,
)
from app.services.rag.fake_llm import FakeLLM


class CustomerChatbotPipeline:
    """고객 질문 검색, 거래 조회, 답변 생성을 연결하는 대응가이드 챗봇."""

    def __init__(self) -> None:
        """챗봇에서 사용할 Fake 임베딩, 검색, 저장소, LLM 서비스를 준비한다."""
        self.embedder = FakeEmbedder()
        self.retriever = FakeGuideRetriever()
        self.transaction_repository = FakeTransactionRepository()
        self.llm = FakeLLM()

    def run(self, request: ChatbotRequestDTO) -> ChatbotResponseDTO:
        """고객 질문과 관련 거래를 바탕으로 대응 가이드 답변을 생성한다."""
        embedding = self.embedder.embed(request.question)
        guide = self.retriever.retrieve(embedding)
        transaction = self.transaction_repository.find_latest_by_user_id(
            request.user_id
        )
        answer = self.llm.generate_chatbot_answer(
            request,
            guide,
            transaction,
        )
        return ChatbotResponseDTO(
            answer=answer,
            related_transaction=transaction,
        )

