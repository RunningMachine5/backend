from app.domain.enums import AgentAction, RiskGrade
from app.dto.agent import AgentResultDTO
from app.dto.fraud import FraudAssessmentDTO
from app.services.notification.fake_email_sender import FakeEmailSender
from app.services.rag.fake_llm import FakeLLM
from app.services.rag.fake_vector_db import FakeVectorDB
from app.services.rag.query_builder import RagQueryBuilder


class MonitoringAgentPipeline:
    """위험등급에 따라 이메일 또는 RAG 대시보드 흐름을 선택한다."""

    def __init__(self) -> None:
        """Agent 분기 이후 사용할 Fake 서비스들을 준비한다."""
        self.email_sender = FakeEmailSender()
        self.query_builder = RagQueryBuilder()
        self.vector_db = FakeVectorDB()
        self.llm = FakeLLM()

    def run(self, assessment: FraudAssessmentDTO) -> AgentResultDTO:
        """매우 높음은 이메일로, 나머지는 RAG 답변으로 처리한다."""
        # 정상 거래는 불필요한 검색과 LLM 생성을 수행하지 않고 즉시 종료한다.
        if not assessment.prediction.is_fraud:
            return AgentResultDTO(
                action=AgentAction.NO_ACTION,
                message="사기 아님으로 분류되어 후속 Agent 파이프라인을 종료합니다.",
            )

        # 최고 위험 거래는 피해자의 즉각적인 확인이 우선이므로 이메일 분기로 전달한다.
        if assessment.risk_grade == RiskGrade.VERY_HIGH:
            email_message = self.email_sender.send_chatbot_url(assessment)
            return AgentResultDTO(
                action=AgentAction.EMAIL_SENT,
                message=email_message,
            )

        # 그 외 위험 거래는 담당자가 판단할 수 있도록 검색 질의와 대응 가이드를 생성한다.
        rag_query = self.query_builder.build(assessment)
        context = self.vector_db.retrieve(rag_query)
        answer = self.llm.generate_monitoring_answer(rag_query, context)
        return AgentResultDTO(
            action=AgentAction.DASHBOARD_REPORTED,
            message=answer,
            rag_query=rag_query,
            retrieved_context=context,
        )
