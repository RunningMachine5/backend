"""채팅 세션 생성과 접속 URL 안내 발송.

설계는 docs/customer-chatbot/README.md 의 2.1 이다.

- 거래 하나당 세션 하나이므로 ``transaction_id`` 기준으로 **멱등**하다. 이미 세션이
  있으면 다시 만들지도, 안내를 다시 보내지도 않는다(``rule_replay`` 재처리 대비).
- 안내는 SMTP 로 실제 발송한다. 메시지 조립과 전송은
  [session_url_mailer.py](session_url_mailer.py)가 맡고 여기서는 수신 주소 결정과
  상태 기록만 한다.
- ``customers.email`` 이 비어 있으면 ``CHAT_FALLBACK_EMAIL`` 로 대신 보낸다
  (스키마 3.9 — 현재 거래 수집 경로가 이메일을 채우지 않는다).
- 발송에 실패하면 세션을 남긴 채 ``status = FAILED`` 로 두고 예외는 삼킨다
  (스키마 3.3). 고객이 URL 을 받지 못했으므로 ``URL_SENT`` 로 기록할 수 없다.

**commit 하지 않는다.** 트랜잭션은 호출부(API 라우터·FDS 파이프라인)가 소유한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Session

from app.core.config import CHAT_FALLBACK_EMAIL
from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.session_url_mailer import (
    ChatSessionUrlMailer,
    ChatSessionUrlNotifier,
)


logger = logging.getLogger(__name__)


# 고령자 전용 UI 분기 기준(PRD 2.1). 출생연도만으로 판정한다.
OLDER_CUSTOMER_AGE = 60


class ChatSessionTargetNotFoundError(LookupError):
    """세션을 만들 거래를 찾지 못한 경우."""


@dataclass(frozen=True, slots=True)
class ChatSessionCreationResult:
    """생성 결과와 실제로 안내를 보낸 주소."""

    chat_session: ChatSession
    # 이번 호출에서 새로 만든 세션인지. 멱등 재호출이면 False 이고 안내도 보내지 않았다.
    created: bool
    notified_email: str | None
    # 기본 주소 폴백으로 보냈는지(``customers.email`` 이 비어 있었는지).
    used_fallback_email: bool
    # 메일이 실제로 나갔는지. False 면 세션은 ``FAILED`` 다.
    email_sent: bool


class ChatSessionCreator:
    """거래 한 건에 대한 채팅 세션을 만들고 접속 URL 을 안내한다."""

    def __init__(
        self,
        session: Session,
        *,
        notifier: ChatSessionUrlNotifier | None = None,
        chat_session_id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.repository = ChatSessionRepository(session)
        # SMTP 클라이언트는 실제 발송 직전까지 만들지 않는다. 멱등 재호출처럼
        # 메일을 보내지 않는 경로가 SMTP 설정 때문에 실패하지 않게 한다.
        self._notifier = notifier
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
        """세션을 만들고 접속 URL 안내를 발송한다.

        이미 세션이 있으면 그대로 돌려주고 안내를 다시 보내지 않는다.
        """

        existing = self.repository.find_by_transaction(transaction_id)
        if existing is not None:
            return ChatSessionCreationResult(
                chat_session=existing,
                created=False,
                notified_email=existing.notified_email,
                used_fallback_email=(
                    existing.notified_email == CHAT_FALLBACK_EMAIL
                ),
                email_sent=existing.email_sent_at is not None,
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
        # 메일 본문의 접속 URL 에 chat_session_id 가 필요하므로 먼저 flush 한다.
        self.session.flush()

        customer_email = _usable_email(customer)
        notified_email = customer_email or CHAT_FALLBACK_EMAIL
        email_sent = self._send_chat_url(
            chat_session=chat_session,
            transaction=transaction,
            customer=customer,
            notified_email=notified_email,
            used_fallback_email=customer_email is None,
        )

        if email_sent:
            self.repository.record_url_sent(
                chat_session,
                notified_email=notified_email,
                email_sent_at=self._now_factory(),
            )
        else:
            # 시도한 주소는 남기고 발송 시각은 비운다(스키마 3.3).
            chat_session.notified_email = notified_email
            self.repository.update_status(
                chat_session,
                ChatSessionStatus.FAILED,
            )

        return ChatSessionCreationResult(
            chat_session=chat_session,
            created=True,
            notified_email=notified_email,
            used_fallback_email=customer_email is None,
            email_sent=email_sent,
        )

    def _send_chat_url(
        self,
        *,
        chat_session: ChatSession,
        transaction: Transaction,
        customer: Customer | None,
        notified_email: str,
        used_fallback_email: bool,
    ) -> bool:
        """B.7 안내 메일을 보낸다. 실패는 로그만 남기고 False 를 돌려준다."""

        try:
            self.notifier.send(
                chat_session_id=chat_session.chat_session_id,
                recipient_email=notified_email,
                customer_name=customer.name if customer is not None else None,
                transaction_datetime=transaction.transaction_datetime,
                transaction_amount=transaction.transaction_amount,
                used_fallback_email=used_fallback_email,
            )
        except Exception:
            logger.exception(
                "챗봇 접속 URL 발송에 실패했습니다: session=%s 수신자=%s",
                chat_session.chat_session_id,
                notified_email,
            )
            return False
        return True

    @property
    def notifier(self) -> ChatSessionUrlNotifier:
        if self._notifier is None:
            self._notifier = ChatSessionUrlMailer.from_env()
        return self._notifier


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
