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
        risk_grade_text = (
            assessment.risk_grade.value
            if assessment.risk_grade
            else "미산정"
        )
        risk_score_text = (
            f"{assessment.risk_score}점"
            if assessment.risk_score is not None
            else "미산정"
        )
        print(f"위험점수: {risk_score_text}")
        print(f"위험등급: {risk_grade_text}")
        if (
            assessment.amount_risk_factor is not None
            and assessment.amount_points is not None
            and assessment.ml_probability_points is not None
        ):
            print(f"- 거래금액 위험도: {assessment.amount_risk_factor:.2f}")
            print(f"- 거래금액 기여점수: {assessment.amount_points:.1f}점")
            print(f"- ML 확률 기여점수: {assessment.ml_probability_points:.1f}점")
        else:
            print("- 위험등급 근거: 정상 거래이므로 미산정")
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
        """Agent가 선택한 작업과 이메일 및 대시보드 내용을 출력한다."""
        print("\n--- AI Agent 처리 결과 ---")
        print(f"수행 작업: {result.action.value}")
        print(result.message)

    def print_chatbot_response(self, response: ChatbotResponseDTO) -> None:
        """고객 대응가이드 챗봇의 최종 답변을 출력한다."""
        print("\n--- 고객 대응가이드 챗봇 ---")
        print(response.answer)
