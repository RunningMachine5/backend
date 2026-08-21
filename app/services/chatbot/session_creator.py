"""거래별 채팅 세션과 알림 수신 주소를 멱등 생성한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Session

from app.core.config import CHAT_FALLBACK_EMAIL
from app.data.model.chatbot import ChatSession
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.repositories.chat_session import ChatSessionRepository


# 고령자 전용 UI 분기 기준
OLDER_CUSTOMER_AGE = 60


class ChatSessionTargetNotFoundError(LookupError):
    """세션을 만들 거래를 찾지 못한 경우."""


@dataclass(frozen=True, slots=True)
class ChatSessionCreationResult:
    """세션 생성 결과와 통합 안내 메일에 사용할 수신 주소."""

    chat_session: ChatSession
    # 이번 호출에서 새로 만든 세션인지 여부
    created: bool
    notified_email: str | None
    # 고객 이메일 대신 기본 주소를 사용했는지 여부
    used_fallback_email: bool


class ChatSessionCreator:
    """거래 한 건에 대한 채팅 세션을 만들고 발송 대상 주소를 계산한다."""

    def __init__(
        self,
        session: Session,
        *,
        chat_session_id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.repository = ChatSessionRepository(session)
        self._chat_session_id_factory = (
            chat_session_id_factory or _generate_chat_session_id
        )
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        transaction_id: int,
        top_fraud_types: list[str] | None = None,
    ) -> ChatSessionCreationResult:
        """세션이 있으면 그대로 반환하고 없으면 새로 만든다."""

        existing = self.repository.find_by_transaction(transaction_id)
        if existing is not None:
            return ChatSessionCreationResult(
                chat_session=existing,
                created=False,
                notified_email=existing.notified_email,
                used_fallback_email=(
                    existing.notified_email == CHAT_FALLBACK_EMAIL
                ),
            )

        transaction = self.session.get(Transaction, transaction_id)
        if transaction is None:
            raise ChatSessionTargetNotFoundError(
                f"채팅 세션을 만들 거래를 찾을 수 없습니다: {transaction_id}"
            )
        customer = (
            self.session.get(Customer, transaction.customer_id)
            if transaction.customer_id is not None
            else None
        )

        chat_session = self.repository.create_or_get(
            chat_session_id=self._chat_session_id_factory(),
            transaction_id=transaction_id,
            top_fraud_types=top_fraud_types,
            is_older=_is_older_customer(customer, now=self._now_factory()),
        )
        # 접속 URL에 사용할 세션 id를 확정한다.
        self.session.flush()

        customer_email = _usable_email(customer)
        notified_email = customer_email or CHAT_FALLBACK_EMAIL
        return ChatSessionCreationResult(
            chat_session=chat_session,
            created=True,
            notified_email=notified_email,
            used_fallback_email=customer_email is None,
        )


def _usable_email(customer: Customer | None) -> str | None:
    """사용 가능한 고객 이메일을 반환한다."""

    if customer is None or customer.email is None:
        return None
    email = customer.email.strip()
    return email or None


def _is_older_customer(customer: Customer | None, *, now: datetime) -> bool:
    """출생연도 기준 고령자 여부를 판정한다."""

    if customer is None:
        return False
    return now.year - customer.birth_date.year >= OLDER_CUSTOMER_AGE


def _generate_chat_session_id() -> str:
    """``CHAT-20260816-A1B2C3D4`` 형식의 세션 id를 생성한다."""

    date_part = datetime.now(UTC).strftime("%Y%m%d")
    return f"CHAT-{date_part}-{uuid4().hex[:8].upper()}"


__all__ = [
    "ChatSessionCreationResult",
    "ChatSessionCreator",
    "ChatSessionTargetNotFoundError",
    "OLDER_CUSTOMER_AGE",
]
