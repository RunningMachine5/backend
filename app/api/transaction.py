from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlmodel import SQLModel, select

from app.core.db import SessionDep
from app.data.model.transaction import Transaction

# FastAPI() 대신 APIRouter(). Spring 의 @RestController + @RequestMapping 에 해당한다.
router = APIRouter(prefix="/transactions", tags=["transactions"])


class TransactionCreate(SQLModel):
    """요청 전용 DTO. 엔티티를 그대로 노출하면 id 를 클라이언트가 주입할 수 있다."""

    payment_method: str


@router.post("", response_model=Transaction)
def create_transaction(payload: TransactionCreate, session: SessionDep) -> Transaction:
    tx = Transaction.model_validate(payload)   # DTO → Entity 매핑 (ModelMapper 역할)
    session.add(tx)                            # em.persist()
    session.commit()                           # 트랜잭션 커밋
    session.refresh(tx)                        # DB 가 채운 id 를 다시 읽어옴
    return tx


@router.get("", response_model=list[Transaction])
def list_transactions(session: SessionDep) -> list[Transaction]:
    # SELECT * FROM transactions ORDER BY id DESC LIMIT 20
    stmt = select(Transaction).order_by(Transaction.id.desc()).limit(20)
    return list(session.exec(stmt).all())