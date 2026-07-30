from app.dto.agent import AgentResultDTO
from app.dto.chatbot import ChatbotResponseDTO
from app.dto.fraud import FraudAssessmentDTO


class ConsoleRenderer:
    """최종 DTO를 금융사 담당자와 고객이 읽을 수 있는 콘솔 형식으로 출력한다."""

    def print_assessment(self, assessment: FraudAssessmentDTO) -> None:
        """거래 식별정보, 분류, 위험등급, 사기유형, 근거를 출력한다."""
        print("\n--- 이상거래 분석 결과 ---")
        print(f"사용자: {assessment.transaction.user_id}")
        print(f"거래시각: {assessment.transaction.transaction_time}")
        print(
            f"사기 분류: {assessment.prediction.is_fraud} "
            f"(확률={assessment.prediction.fraud_probability:.2f})"
        )
        print(f"위험등급: {assessment.risk_grade.value}")
        fraud_type_text = (
            assessment.primary_fraud_type.value
            if assessment.primary_fraud_type
            else "해당 없음"
        )
        print(f"대표 사기유형: {fraud_type_text}")
        print("근거:")
        for evidence in assessment.evidence:
            print(f"- {evidence}")

    def print_agent_result(self, result: AgentResultDTO) -> None:
        """Agent가 선택한 작업과 이메일 또는 대시보드 내용을 출력한다."""
        print("\n--- AI Agent 처리 결과 ---")
        print(f"수행 작업: {result.action.value}")
        print(result.message)

    def print_chatbot_response(self, response: ChatbotResponseDTO) -> None:
        """고객 대응가이드 챗봇의 최종 답변을 출력한다."""
        print("\n--- 고객 대응가이드 챗봇 ---")
        print(response.answer)
