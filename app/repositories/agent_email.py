"""이상거래 안내 이메일에 필요한 고객·거래 정보를 조회한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from app.core.config import CHAT_FALLBACK_EMAIL
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction


@dataclass(frozen=True, slots=True)
class FraudAlertEmailContext:
    """고객 이메일과 안내문에 표시할 거래정보이다."""

    recipient_email: str
    customer_name: str
    transaction_datetime: datetime
    transaction_amount: int
    channel: str


class AgentEmailRepository:
    """거래 ID로 이메일 발송 문맥을 조회한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_email_context(
        self,
        transaction_id: int,
    ) -> FraudAlertEmailContext | None:
        transaction = self.session.get(Transaction, transaction_id)
        if transaction is None:
            return None

        customer = self.session.get(Customer, transaction.customer_id)
        if customer is None:
            return None

        # 값이 없으면 CHAT_FALLBACK_EMAIL 로 보낼건데 "  "이런 공백문자는 None 처리가 안되기때문에 통일
        customer_email = (customer.email or "").strip()

        return FraudAlertEmailContext(
            # 이메일이 없으면 CHAT_FALLBACK_EMAIL 에 있는 기본 메일 주소로 보낸다
            recipient_email=customer_email or CHAT_FALLBACK_EMAIL,
            customer_name=customer.name,
            transaction_datetime=transaction.transaction_datetime,
            transaction_amount=transaction.transaction_amount,
            channel=transaction.channel,
        )


__all__ = ["AgentEmailRepository", "FraudAlertEmailContext"]
