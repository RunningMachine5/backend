from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import agent_case, chat, dashboard_graph, fraud_rule, health, mlops, transaction, dashboard_insight
from app.core.config import ML_SERVER_URL
from app.core.exception_handlers import register_exception_handlers
from app.services.ml_serving.predict_client import MLServingClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ml_serving_client = MLServingClient(base_url = ML_SERVER_URL)

    yield

    await app.state.ml_serving_client.aclose()

app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)

# 라우터 등록. 파일이 늘어나면 여기에 include_router 만 추가하면 된다.
app.include_router(health.router)
app.include_router(transaction.router)
app.include_router(chat.router)
app.include_router(chat.agent_router)
app.include_router(mlops.router)
app.include_router(fraud_rule.router)
app.include_router(dashboard_insight.router)
app.include_router(agent_case.router)
app.include_router(dashboard_graph.router)


@app.get("/")
async def root():
    return {"server-message": "Hello 준혁,덕현,강현,정현,주현"}

