from app.dto.fraud import FraudAssessmentDTO


class FakeEmailSender:
    """실제 이메일 API 대신 발송 내용을 문자열로 생성하는 서비스."""

    def send_chatbot_url(self, assessment: FraudAssessmentDTO) -> str:
        """매우 높은 위험 거래의 사용자에게 보낼 챗봇 URL 안내문을 만든다."""
        chatbot_url = (
            f"https://fake-finance.local/chatbot"
        )
        return (
            f"수신자 {assessment.transaction.email}: 매우 높은 위험 거래가 탐지되었습니다. "
            f"아래 챗봇에서 거래 확인 및 대응 안내를 받아주세요. {chatbot_url}"
            f"고객 이름 : {assessment.transaction.user_name}, "
            f"이메일 : {assessment.transaction.email}"
            f"거래 시간 : {assessment.transaction.transaction_time}, "
            f"거래 금액 : {assessment.transaction.amount}, "
            f"의심 사기 유형 : {assessment.primary_fraud_type.value}, "
        )

