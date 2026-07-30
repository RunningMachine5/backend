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

    def test_other_risk_uses_rag_branch(self) -> None:
        """두 번째 거래가 RAG 기반 대시보드 분기로 이어지는지 확인한다."""
        assessment = FraudDetectionPipeline().run(FAKE_TRANSACTIONS[1])
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertNotEqual(assessment.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(agent_result.action, AgentAction.DASHBOARD_REPORTED)
        self.assertIn("[유사사례/대응가이드]", agent_result.message)

    def test_non_fraud_stops_before_pattern_analysis(self) -> None:
        """사기 아님 거래가 패턴 분석 없이 조치 없음으로 종료되는지 확인한다."""
        transaction = TransactionDTO(
            user_id="USR_SAFE",
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

    def test_chatbot_combines_guide_and_transaction(self) -> None:
        """고객 질문에 관련 거래정보와 고객 대응 가이드가 포함되는지 확인한다."""
        response = CustomerChatbotPipeline().run(
            ChatbotRequestDTO(
                user_id="USR_100123",
                question="이 거래는 제가 하지 않았습니다.",
            )
        )

        self.assertIn("85,000,000원", response.answer)
        self.assertIn("즉시 중지", response.answer)


if __name__ == "__main__":
    unittest.main()
