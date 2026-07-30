from app.dto.agent import RagQueryDTO, RetrievedContextDTO
from app.dto.chatbot import ChatbotRequestDTO, CustomerGuideDTO
from app.dto.transaction import TransactionDTO


class FakeLLM:
    """외부 LLM API 없이 전달받은 문맥을 템플릿으로 통합한다."""

    def generate_monitoring_answer(
        self,
        query: RagQueryDTO,
        context: RetrievedContextDTO,
    ) -> str:
        """RAG 질의와 검색 문맥을 담당자용 최종 답변으로 조합한다."""
        return (
            f"[위험등급] {query.risk_grade.value}\n"
            f"[사기유형] {query.fraud_type.value}\n"
            f"[탐지근거] {', '.join(query.evidence)}\n"
            f"[유사사례/대응가이드] {context.title}: {context.content}"
        )

    def generate_chatbot_answer(
        self,
        request: ChatbotRequestDTO,
        guide: CustomerGuideDTO,
        transaction: TransactionDTO,
    ) -> str:
        """고객 질문, 대응 가이드, 관련 거래를 고객용 답변으로 조합한다."""
        return (
            f"문의하신 거래는 {transaction.transaction_time}에 "
            f"{transaction.merchant_category} 업종에서 발생한 "
            f"{transaction.amount:,}원 {transaction.payment_method} 결제입니다. "
            f"{guide.content}"
        )

