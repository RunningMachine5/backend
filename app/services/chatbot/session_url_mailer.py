"""챗봇 접속 URL 안내 메일을 조립해 SMTP로 보낸다.

설계는 docs/customer-chatbot/README.md 2.1(발송 구현과 기본 주소 폴백)과
messages.md B.7 이다. 문구는 [messages.py](messages.py)에만 있고 여기서는 조립만 한다.

전송 수단은 Agent 이상거래 안내 메일이 쓰는
[SmtpEmailMessageSender](../agent/email_sender.py)를 그대로 재사용한다. 두 기능이
같은 SMTP 계정(``SMTP_*``)으로 나가므로 클라이언트를 따로 두지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol

from app.core.config import (
    CHAT_BASE_URL,
    SMTP_FROM_EMAIL,
    SMTP_FROM_NAME,
    SMTP_USERNAME,
)
from app.services.agent.email_sender import (
    EmailMessageSender,
    SmtpEmailMessageSender,
)
from app.services.chatbot.messages import (
    CHAT_URL_EMAIL_SUBJECT,
    render_chat_url_email_body,
)


logger = logging.getLogger(__name__)


class ChatSessionUrlNotifier(Protocol):
    """세션 접속 URL 안내를 고객에게 전달하는 계약."""

    def send(
        self,
        *,
        chat_session_id: str,
        recipient_email: str,
        customer_name: str | None,
        transaction_datetime: datetime,
        transaction_amount: int,
        used_fallback_email: bool,
    ) -> None: ...


class ChatSessionUrlMailer:
    """B.7 메일을 만들어 SMTP로 발송한다. 실패하면 예외를 그대로 올린다."""

    def __init__(
        self,
        sender: EmailMessageSender,
        *,
        from_email: str,
        from_name: str = "FDShield",
    ) -> None:
        self.sender = sender
        self.from_email = from_email
        self.from_name = from_name

    @classmethod
    def from_env(cls) -> "ChatSessionUrlMailer":
        """Agent 메일과 같은 ``SMTP_*`` 설정으로 발송기를 만든다."""

        return cls(
            SmtpEmailMessageSender.from_env(),
            from_email=SMTP_FROM_EMAIL or SMTP_USERNAME,
            from_name=SMTP_FROM_NAME,
        )

    def send(
        self,
        *,
        chat_session_id: str,
        recipient_email: str,
        customer_name: str | None,
        transaction_datetime: datetime,
        transaction_amount: int,
        used_fallback_email: bool,
    ) -> None:
        chat_url = build_chat_url(chat_session_id)
        self.sender.send(
            build_chat_url_email_message(
                chat_session_id=chat_session_id,
                recipient_email=recipient_email,
                customer_name=customer_name,
                transaction_datetime=transaction_datetime,
                transaction_amount=transaction_amount,
                from_email=self.from_email,
                from_name=self.from_name,
            )
        )
        # 데모·로컬에서 어떤 주소로 어떤 URL이 나갔는지 눈으로 확인하기 위한 기록이다.
        recipient = (
            f"{recipient_email} (기본 주소)"
            if used_fallback_email
            else recipient_email
        )
        logger.info(
            "[챗봇 URL 발송] 수신자=%s 세션=%s URL=%s",
            recipient,
            chat_session_id,
            chat_url,
        )


def build_chat_url_email_message(
    *,
    chat_session_id: str,
    recipient_email: str,
    customer_name: str | None,
    transaction_datetime: datetime,
    transaction_amount: int,
    from_email: str,
    from_name: str,
) -> EmailMessage:
    """B.7 제목·본문에 거래 원장 값과 세션 접속 URL을 채운다."""

    message = EmailMessage()
    message["Subject"] = CHAT_URL_EMAIL_SUBJECT
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = recipient_email
    message.set_content(
        render_chat_url_email_body(
            customer_name=customer_name,
            transaction_datetime=transaction_datetime,
            transaction_amount=transaction_amount,
            chat_url=build_chat_url(chat_session_id),
        )
    )
    return message


def build_chat_url(chat_session_id: str) -> str:
    """고객이 접속할 챗봇 URL(PRD 2.1).

    ``CHAT_BASE_URL``은 챗봇 화면을 서빙하는 주소이며, 프론트가 화면을 가지고 있으면
    Backend 주소가 아니라 프론트 주소를 넣는다.
    """

    return f"{CHAT_BASE_URL}/chat/{chat_session_id}"


__all__ = [
    "ChatSessionUrlMailer",
    "ChatSessionUrlNotifier",
    "build_chat_url",
    "build_chat_url_email_message",
]
