"""이상거래 안내 이메일에 필요한 고객·거래 정보를 조회한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

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
        transaction_id: str,
    ) -> FraudAlertEmailContext | None:
        transaction = self.session.get(Transaction, transaction_id)
        if transaction is None:
            return None

        customer = self.session.get(Customer, transaction.customer_id)
        if customer is None or not customer.email:
            return None

        return FraudAlertEmailContext(
            recipient_email=customer.email,
            customer_name=customer.personal_identifier,
            transaction_datetime=transaction.transaction_datetime,
            transaction_amount=transaction.transaction_amount,
            channel=transaction.channel,
        )


__all__ = ["AgentEmailRepository", "FraudAlertEmailContext"]
