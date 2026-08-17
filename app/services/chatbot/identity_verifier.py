"""챗봇 접속 시의 간이 본인인증.

설계는 docs/customer-chatbot/README.md 2.2 다. 고객이 입력한 **출생연도 4자리**를
세션에 연결된 거래의 고객(``customers.birth_date``)과 대조한다.

실패 횟수 제한·URL 토큰·세션 TTL 은 MVP 범위 밖이며(PRD 3.3), 인증에 성공해도
발급하는 토큰이 없다.
"""

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
    """입력한 출생연도가 세션 고객의 출생연도와 같은지 판정한다.

    거래나 고객을 찾지 못하면 대조할 값이 없으므로 실패로 본다.
    """

    transaction = session.get(Transaction, chat_session.transaction_id)
    if transaction is None or transaction.customer_id is None:
        return False

    customer = session.get(Customer, transaction.customer_id)
    if customer is None:
        return False

    return f"{customer.birth_date.year:04d}" == birth_year.strip()


__all__ = ["verify_birth_year"]
