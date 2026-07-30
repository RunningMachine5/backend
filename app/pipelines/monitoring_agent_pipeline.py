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
        if not assessment.prediction.is_fraud:
            return AgentResultDTO(
                action=AgentAction.NO_ACTION,
                message="사기 아님으로 분류되어 후속 Agent 파이프라인을 종료합니다.",
            )

        if assessment.risk_grade == RiskGrade.VERY_HIGH:
            email_message = self.email_sender.send_chatbot_url(assessment)
            return AgentResultDTO(
                action=AgentAction.EMAIL_SENT,
                message=email_message,
            )

        rag_query = self.query_builder.build(assessment)
        context = self.vector_db.retrieve(rag_query)
        answer = self.llm.generate_monitoring_answer(rag_query, context)
        return AgentResultDTO(
            action=AgentAction.DASHBOARD_REPORTED,
            message=answer,
        )
