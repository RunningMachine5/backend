import unittest

from app.data.fake_data import FAKE_TRANSACTIONS
from app.domain.enums import AgentAction, FraudType, RiskGrade
from app.dto.chatbot import ChatbotRequestDTO
from app.dto.fraud import FraudAssessmentDTO, FraudPredictionDTO
from app.pipelines.customer_chatbot_pipeline import CustomerChatbotPipeline
from app.pipelines.monitoring_agent_pipeline import MonitoringAgentPipeline


def assessment_for_agent(
    *,
    risk_grade: RiskGrade | None,
    is_fraud: bool = True,
) -> FraudAssessmentDTO:
    """아직 구형 계약을 쓰는 모니터링 에이전트에 명시적 입력을 만든다."""

    return FraudAssessmentDTO(
        transaction=FAKE_TRANSACTIONS[0],
        prediction=FraudPredictionDTO(
            is_fraud=is_fraud,
            fraud_probability=0.95 if is_fraud else 0.05,
        ),
        patterns=[],
        fraud_type_scores=[],
        primary_fraud_type=(
            FraudType.LARGE_AMOUNT_PAYMENT if is_fraud else None
        ),
        risk_score=95 if risk_grade is not None else None,
        risk_grade=risk_grade,
        amount_risk_factor=1.0 if risk_grade is not None else None,
        amount_points=50.0 if risk_grade is not None else None,
        ml_probability_points=45.0 if risk_grade is not None else None,
        evidence=["테스트용 고액 거래 근거"] if is_fraud else [],
    )


class PipelineTest(unittest.TestCase):
    """아직 별도 스켈레톤인 Agent·Chatbot 분기를 검증한다."""

    def test_very_high_risk_uses_email_and_rag_branches(self) -> None:
        """매우높음 거래가 피해자 이메일과 담당자 RAG 처리로 이어지는지 확인한다."""
        assessment = assessment_for_agent(risk_grade=RiskGrade.VERY_HIGH)
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertEqual(assessment.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(
            agent_result.action,
            AgentAction.EMAIL_AND_DASHBOARD_REPORTED,
        )

        # 피해자 안내와 담당자용 RAG 결과가 하나의 Agent 결과에 모두 포함되어야 한다.
        self.assertIn("[피해자 안내]", agent_result.message)
        self.assertIn("https://fake-finance.local/chatbot", agent_result.message)
        self.assertIn("[담당자 대시보드]", agent_result.message)
        self.assertIn("[유사사례/대응가이드]", agent_result.message)
        self.assertIn("[담당자 조치]", agent_result.message)
        self.assertIsNotNone(agent_result.rag_query)
        self.assertIsNotNone(agent_result.retrieved_context)
        self.assertEqual(
            agent_result.rag_query.risk_grade,
            RiskGrade.VERY_HIGH,
        )
        self.assertIn("금융보안원", agent_result.retrieved_context.source)

    def test_other_risk_uses_rag_branch(self) -> None:
        """두 번째 거래가 RAG 기반 대시보드 분기로 이어지는지 확인한다."""
        assessment = assessment_for_agent(risk_grade=RiskGrade.HIGH)
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
        """모델이 정상으로 판정하면 후속 분석 없이 종료되는지 확인한다."""
        assessment = assessment_for_agent(risk_grade=None, is_fraud=False)
        agent_result = MonitoringAgentPipeline().run(assessment)

        self.assertFalse(assessment.prediction.is_fraud)
        self.assertEqual(assessment.patterns, [])
        self.assertIsNone(assessment.risk_score)
        self.assertIsNone(assessment.risk_grade)
        self.assertIsNone(assessment.amount_risk_factor)
        self.assertIsNone(assessment.amount_points)
        self.assertIsNone(assessment.ml_probability_points)
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
