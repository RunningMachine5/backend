"""거래에 연결된 채팅 세션을 멱등 생성한다.

설계는 docs/customer-chatbot/README.md 의 2.1 이다.

- 거래 하나당 세션 하나이므로 ``transaction_id`` 기준으로 **멱등**하다. 이미 세션이
  있으면 다시 만들지 않는다(``rule_replay`` 재처리 대비).
- 고객 안내 메일과 발송 결과에 따른 상태 기록은
  [session_alert_notifier.py](session_alert_notifier.py)가 맡는다.
- ``customers.email`` 이 비어 있으면 호출부가 사용할 ``CHAT_FALLBACK_EMAIL`` 을
  수신 예정 주소로 반환한다(스키마 3.9).

**commit 하지 않는다.** 트랜잭션은 호출부(API 라우터·FDS 파이프라인)가 소유한다.
"""

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


# 고령자 전용 UI 분기 기준(PRD 2.1). 출생연도만으로 판정한다.
OLDER_CUSTOMER_AGE = 60


class ChatSessionTargetNotFoundError(LookupError):
    """세션을 만들 거래를 찾지 못한 경우."""


@dataclass(frozen=True, slots=True)
class ChatSessionCreationResult:
    """세션 생성 결과와 통합 안내 메일에 사용할 수신 주소."""

    chat_session: ChatSession
    # 이번 호출에서 새로 만든 세션인지. 멱등 재호출이면 False 이고 안내도 보내지 않았다.
    created: bool
    notified_email: str | None
    # 기본 주소 폴백으로 보냈는지(``customers.email`` 이 비어 있었는지).
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
        # Agent 통합 메일의 접속 URL에 chat_session_id가 필요하므로 먼저 flush 한다.
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
    """``NULL``·빈 문자열·공백뿐인 값은 주소가 없는 것으로 본다(PRD 2.1)."""

    if customer is None or customer.email is None:
        return None
    email = customer.email.strip()
    return email or None


def _is_older_customer(customer: Customer | None, *, now: datetime) -> bool:
    """출생연도 기준 60세 이상인지 판정한다(PRD 2.1)."""

    if customer is None:
        return False
    return now.year - customer.birth_date.year >= OLDER_CUSTOMER_AGE


def _generate_chat_session_id() -> str:
    """``CHAT-20260816-A1B2C3D4`` 형식의 세션 id(Agent 사건 id 와 같은 규칙)."""

    date_part = datetime.now(UTC).strftime("%Y%m%d")
    return f"CHAT-{date_part}-{uuid4().hex[:8].upper()}"


__all__ = [
    "ChatSessionCreationResult",
    "ChatSessionCreator",
    "ChatSessionTargetNotFoundError",
    "OLDER_CUSTOMER_AGE",
]
