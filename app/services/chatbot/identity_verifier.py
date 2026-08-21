"""입력한 출생연도와 세션 고객의 출생연도를 대조한다."""

from __future__ import annotations

from sqlmodel import Session

from app.data.model.chatbot import ChatSession
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction


def verify_birth_year(
    session: Session,
    chat_session: ChatSession,
    birth_year: str,
) -> bool:
    """거래와 고객이 존재하고 출생연도가 일치하면 참을 반환한다."""

    transaction = session.get(Transaction, chat_session.transaction_id)
    if transaction is None or transaction.customer_id is None:
        return False

    customer = session.get(Customer, transaction.customer_id)
    if customer is None:
        return False

    return f"{customer.birth_date.year:04d}" == birth_year.strip()


__all__ = ["verify_birth_year"]
