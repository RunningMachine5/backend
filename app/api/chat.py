from fastapi import APIRouter
from sqlmodel import SQLModel
from app.core.db import SessionDep
from app.services.chatbot.customer_chatbot import build_chatbot_chain

router = APIRouter(prefix="/chat", tags=["chat"])

class AskRequest(SQLModel):
    """챗봇 질문 DTO"""
    user_question: str

class AskResponse(SQLModel):
    """챗봇 응답 DTO"""
    ai_answer: str

@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, session: SessionDep) -> AskResponse:
    """챗봇 질의 엔드포인트"""
    answer=build_chatbot_chain(session).invoke(payload.user_question)
    return AskResponse(ai_answer=answer)