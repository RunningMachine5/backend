from app.domain.enums import AgentAction, RiskGrade
from app.dto.legacy_agent import AgentResultDTO
from app.dto.fraud import FraudAssessmentDTO
from app.services.notification.fake_email_sender import FakeEmailSender
from app.services.rag.fake_llm import FakeLLM
from app.services.rag.fake_vector_db import FakeVectorDB
from app.services.rag.query_builder import RagQueryBuilder


class MonitoringAgentPipeline:
    """위험등급에 따라 이메일과 RAG 대시보드 후속 작업을 수행한다."""

    def __init__(self) -> None:
        """Agent 분기 이후 사용할 Fake 서비스들을 준비한다."""
        self.email_sender = FakeEmailSender()
        self.query_builder = RagQueryBuilder()
        self.vector_db = FakeVectorDB()
        self.llm = FakeLLM()

    def run(self, assessment: FraudAssessmentDTO) -> AgentResultDTO:
        """매우 높음은 이메일과 RAG로, 나머지 사기 거래는 RAG로 처리한다."""
        # 정상 거래는 불필요한 검색과 LLM 생성을 수행하지 않고 즉시 종료한다.
        if not assessment.prediction.is_fraud:
            return AgentResultDTO(
                action=AgentAction.NO_ACTION,
                message="사기 아님으로 분류되어 후속 Agent 파이프라인을 종료합니다.",
            )

        email_message = None

        # 최고 위험 거래는 피해자에게 즉시 알리되 담당자용 RAG 처리도 계속 수행한다.
        if assessment.risk_grade == RiskGrade.VERY_HIGH:
            email_message = self.email_sender.send_chatbot_url(assessment)

        # 모든 사기 의심 거래에 대해 담당자가 확인할 검색 질의와 대응 가이드를 생성한다.
        rag_query = self.query_builder.build(assessment)
        context = self.vector_db.retrieve(rag_query)
        dashboard_answer = self.llm.generate_monitoring_answer(rag_query, context)

        # VERY_HIGH 거래는 피해자 안내와 담당자 대시보드 결과를 함께 반환한다.
        if email_message is not None:
            return AgentResultDTO(
                action=AgentAction.EMAIL_AND_DASHBOARD_REPORTED,
                message=(
                    f"[피해자 안내]\n{email_message}\n\n"
                    f"[담당자 대시보드]\n{dashboard_answer}"
                ),
                rag_query=rag_query,
                retrieved_context=context,
            )

        return AgentResultDTO(
            action=AgentAction.DASHBOARD_REPORTED,
            message=dashboard_answer,
            rag_query=rag_query,
            retrieved_context=context,
        )
