"""고객 대응 가이드 제공 챗봇"""
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableParallel,RunnablePassthrough
from dotenv import load_dotenv
import os

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    print("openai 키가 인식되지 않습니다")

def fake_retriever(question: str):
    return "사기를 당하면 즉시 은행과 112에 전화하는게 좋습니다"

prompt=ChatPromptTemplate.from_messages([
    ("system","당신은 금융사기를 당한 피해자에게 대응 가이드를 알려주는 챗봇이야"),
    ("human","[참고 지식]: {context}\n\n[질문]: {question}\n\n 참고 지식을 참고해서 질문에 대답해줘.")
])

model = ChatOpenAI(model="gpt-4o", temperature=0.1)

parser = StrOutputParser()

#Runnable을 병렬로 실행해서 dict 형태로 합친다
rag_input_chain = RunnableParallel({
    "context": fake_retriever,
    "question": RunnablePassthrough(),# 입력을 question으로 그대로 넘겨줌
})

llm_chain= rag_input_chain | prompt | model | parser

answer=llm_chain.invoke("보이스 피싱을 당했어 어떻게 해야하나?")
print(answer)