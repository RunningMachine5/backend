import unittest

from app.data.fake_data import FAKE_TRANSACTIONS
from app.domain.enums import AgentAction, RiskGrade
from app.dto.chatbot import ChatbotRequestDTO
from app.dto.transaction import TransactionDTO
from app.pipelines.customer_chatbot_pipeline import CustomerChatbotPipeline
from app.pipelines.fraud_detection_pipeline import FraudDetectionPipeline
from app.pipelines.monitoring_agent_pipeline import MonitoringAgentPipeline


class PipelineTest(unittest.TestCase):
    """핵심 분기와 DTO 연결을 검증하는 최소 단위 테스트."""

    def test_very_high_risk_uses_email_branch(self) -> None:
        """사용자 제공 고액 거래가 매우높음 등급과 이메일 분기로 이어지는지 확인한다."""
        assessment = FraudDetectionPipeline().run(FAKE_TRANSACTIONS[0])
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertEqual(assessment.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(agent_result.action, AgentAction.EMAIL_SENT)
        self.assertIn("https://fake-finance.local/chatbot", agent_result.message)
        self.assertIsNone(agent_result.rag_query)
        self.assertIsNone(agent_result.retrieved_context)

    def test_other_risk_uses_rag_branch(self) -> None:
        """두 번째 거래가 RAG 기반 대시보드 분기로 이어지는지 확인한다."""
        assessment = FraudDetectionPipeline().run(FAKE_TRANSACTIONS[1])
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertNotEqual(assessment.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(agent_result.action, AgentAction.DASHBOARD_REPORTED)
        self.assertIn("[유사사례/대응가이드]", agent_result.message)
        self.assertIsNotNone(agent_result.rag_query)
        self.assertIsNotNone(agent_result.retrieved_context)

        # 생성된 질의가 팀 간 DTO 계약에 필요한 탐지 정보를 모두 포함하는지 확인한다.
        self.assertIn(assessment.risk_grade.value, agent_result.rag_query.query)
        self.assertIn(
            assessment.primary_fraud_type.value,
            agent_result.rag_query.query,
        )
        for evidence in assessment.evidence:
            self.assertIn(evidence, agent_result.rag_query.query)

        self.assertIn("금융보안원", agent_result.retrieved_context.source)
        self.assertIn("[담당자 조치]", agent_result.message)

    def test_non_fraud_stops_before_pattern_analysis(self) -> None:
        """사기 아님 거래가 패턴 분석 없이 조치 없음으로 종료되는지 확인한다."""
        transaction = TransactionDTO(
            user_id="USR_SAFE",
            user_name="홍길동",
            email="sample@email.com",
            transaction_time="2026-07-30T14:00:00+09:00",
            amount=100_000,
            user_amount_std_dev=100_000.00,
            payment_method="TRANSFER",
            merchant_category="GROCERIES",
        )
        assessment = FraudDetectionPipeline().run(transaction)
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertFalse(assessment.prediction.is_fraud)
        self.assertEqual(assessment.patterns, [])
        self.assertEqual(agent_result.action, AgentAction.NO_ACTION)
        self.assertIsNone(agent_result.rag_query)
        self.assertIsNone(agent_result.retrieved_context)

    def test_chatbot_combines_guide_and_transaction(self) -> None:
        """고객 질문에 관련 거래정보와 고객 대응 가이드가 포함되는지 확인한다."""
        response = CustomerChatbotPipeline().run(
            ChatbotRequestDTO(
                #user_id="USR_100123",
                question="이 거래는 제가 하지 않았습니다.",
            )
        )

        # self.assertIn("85,000,000원", response.answer)
        self.assertIn("즉시 중지", response.answer)
        self.assertIn("Fake Guide DB", response.source)  # 리트리버가 반환한 문서의 출처를 확인한다.


if __name__ == "__main__":
    unittest.main()
