"""고객 대응 가이드 제공 챗봇"""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langchain_openai import ChatOpenAI
from sqlmodel import Session

from app.core.db import engine
from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.chatbot_retriever import retriever_source

prompt=ChatPromptTemplate.from_messages([
    ("system","당신은 금융사기를 당한 피해자에게 대응 가이드를 알려주는 챗봇이야"),
    ("human","[참고 지식]: {context}\n\n[질문]: {question}\n\n 참고 지식을 참고해서 질문에 대답해줘.")
])

model = ChatOpenAI(model="gpt-4o", temperature=0.1)

parser = StrOutputParser()


def _render_retrieved_context(
    chunks: list[RetrievedChatbotGuideChunkDTO],
) -> str:
    """구조화된 검색 결과를 기존 RAG 프롬프트의 출처 형식으로 변환한다."""

    return "\n\n".join(
        (
            f"[출처: {chunk.source_title[:20]}"
            f"{f' {chunk.page}p' if chunk.page is not None else ''}]\n"
            f"{chunk.content}"
        )
        for chunk in chunks
    )


# 여기에 들어가는 Session 은 Fast API 레벨에서 넣어줌
def build_chatbot_chain(session: Session,top_k: int =3) -> Runnable:
    """
    질문 문자열을 받아 답변 문자열을 돌려주는 RAG 체인을 만든다
    """
    # RunnableLambda 로 감싸야 일반 함수도 체인의 한 단계로 들어갈 수 있다
    retrieve = RunnableLambda(
        lambda question: _render_retrieved_context(
            retriever_source(question, session, top_k=top_k)
        )
    )

    rag_input_chain = {
        "context": retrieve,
        "question": RunnablePassthrough(),  # 입력을 question으로 그대로 넘겨줌
    }

    return rag_input_chain | prompt | model | parser

def main():
    """로컬 테스트용"""
    with Session(engine) as session:
        answer = build_chatbot_chain(session).invoke("보이스피싱 사기를 당했어 어떻게하지")
        print(answer)

if __name__ == "__main__":
    main()
