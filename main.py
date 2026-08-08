from fastapi import FastAPI

from app.api import chat, fraud_rule, health, mlops, transaction

app = FastAPI()

# 라우터 등록. 파일이 늘어나면 여기에 include_router 만 추가하면 된다.
app.include_router(health.router)
app.include_router(transaction.router)
app.include_router(chat.router)
app.include_router(mlops.router)
app.include_router(fraud_rule.router)


@app.get("/")
async def root():
    return {"server-message": "Hello 준혁,덕현,강현,정현,주현"}

