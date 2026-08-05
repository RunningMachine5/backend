from fastapi import FastAPI

from app.api import health, transaction,chat
from app.data.fake_data import FAKE_TRANSACTIONS
from app.pipelines.customer_chatbot_pipeline import CustomerChatbotPipeline
from app.pipelines.fraud_detection_pipeline import FraudDetectionPipeline
from app.pipelines.monitoring_agent_pipeline import MonitoringAgentPipeline
from app.presentation.console_renderer import ConsoleRenderer
from app.dto.chatbot import ChatbotRequestDTO

app = FastAPI()

# 라우터 등록. 파일이 늘어나면 여기에 include_router 만 추가하면 된다.
app.include_router(health.router)
app.include_router(transaction.router)
app.include_router(chat.router)


@app.get("/")
async def root():
    return {"server-message": "Hello 준혁,덕현,강현,정현,주현"}


def main() -> None:
    """두 위험도 분기와 고객 대응 챗봇의 전체 데모를 순서대로 실행한다."""
    fraud_pipeline = FraudDetectionPipeline()
    agent_pipeline = MonitoringAgentPipeline()
    chatbot_pipeline = CustomerChatbotPipeline()
    renderer = ConsoleRenderer()

    print("=== 금융 이상거래 탐지 및 대응 파이프라인 ===")
    for transaction in FAKE_TRANSACTIONS: # 신규 거래 데이터
        assessment = fraud_pipeline.run(transaction) # 여기가 ML 시작
        renderer.print_assessment(assessment)

        agent_result = agent_pipeline.run(assessment)
        renderer.print_agent_result(agent_result)

    chatbot_request = ChatbotRequestDTO(
        # user_id="USR_100123",
        question="방금 발생한 고액 카드 거래가 제가 한 거래가 아닌데 어떻게 해야 하나요?",
    )
    chatbot_response = chatbot_pipeline.run(chatbot_request)
    renderer.print_chatbot_response(chatbot_response)


if __name__ == "__main__":
    main()

