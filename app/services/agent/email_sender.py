"""이상거래 고객 안내 이메일을 SMTP로 발송한다."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.domain.fraud_type_codes import get_fraud_type_display_name
from app.dto.agent import FraudAlertEmailCommand
from app.repositories.agent_email import (
    AgentEmailRepository,
    FraudAlertEmailContext,
)


class EmailMessageSender(Protocol):
    """완성된 이메일 메시지를 외부 메일 서버로 전달하는 계약이다."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpEmailMessageSender:
    """환경변수로 설정된 SMTP 서버를 이용하는 실제 발송 구현체이다."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout_seconds: float = 7,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "SmtpEmailMessageSender":
        return cls(
            host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.getenv("SMTP_PORT", "587")),
            username=os.getenv("SMTP_USERNAME", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            timeout_seconds=float(os.getenv("SMTP_TIMEOUT_SECONDS", "7")),
        )

    def send(self, message: EmailMessage) -> None:
        if not self.username or not self.password:
            raise RuntimeError("SMTP 계정 정보가 설정되지 않았다.")

        with smtplib.SMTP(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
        ) as smtp:
            smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(message)


class FraudAlertEmailService:
    """이메일 문맥을 조회하고 이상거래 안내 메시지를 발송한다."""

    def __init__(
        self,
        repository: AgentEmailRepository,
        sender: EmailMessageSender,
        *,
        from_email: str,
        from_name: str = "FDShield",
        chatbot_url: str,
    ) -> None:
        self.repository = repository
        self.sender = sender
        self.from_email = from_email
        self.from_name = from_name
        self.chatbot_url = chatbot_url

    @classmethod
    def from_env(cls, repository: AgentEmailRepository) -> "FraudAlertEmailService":
        sender = SmtpEmailMessageSender.from_env()
        from_email = os.getenv("SMTP_FROM_EMAIL", sender.username)
        return cls(
            repository,
            sender,
            from_email=from_email,
            from_name=os.getenv("SMTP_FROM_NAME", "FDShield"),
            chatbot_url=os.getenv(
                "CUSTOMER_CHATBOT_URL",
                # 아래에 주소 바꾸면 됩니다.
                "http://localhost:3000/customer-chat",
            ),
        )

    def send(self, command: FraudAlertEmailCommand) -> None:
        context = self.repository.get_email_context(command.transaction_id)
        if context is None:
            return

        self.sender.send(
            build_fraud_alert_email_message(
                command,
                context,
                from_email=self.from_email,
                from_name=self.from_name,
                chatbot_url=self.chatbot_url,
            )
        )


class NoOpFraudAlertEmailService:
    """이메일 연동이 없는 테스트에서 사용하는 기본 구현체이다."""

    def send(self, command: FraudAlertEmailCommand) -> None:
        del command


def build_fraud_alert_email_message(
    command: FraudAlertEmailCommand,
    context: FraudAlertEmailContext,
    *,
    from_email: str,
    from_name: str,
    chatbot_url: str,
) -> EmailMessage:
    """정해진 템플릿에 거래정보와 상위 의심 유형을 채운다."""

    primary_type = get_fraud_type_display_name(command.primary_suspected_type)
    secondary_type = get_fraud_type_display_name(command.secondary_suspected_type)
    transaction_time = context.transaction_datetime.strftime("%Y-%m-%d %H:%M:%S")
    amount = f"{abs(context.transaction_amount):,}원"

    message = EmailMessage()
    message["Subject"] = "[FDShield] 이상거래 의심 거래 확인 요청"
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = context.recipient_email
    message.set_content(
        f"""{context.customer_name} 고객님, 이상거래로 의심되는 거래가 탐지되었습니다.

거래 일시: {transaction_time}
거래 금액: {amount}
거래 채널: {context.channel}
1순위 의심 유형: {primary_type}
2순위 의심 유형: {secondary_type}

본인이 요청한 거래인지 확인해 주시기 바랍니다.

아래 FDShield 고객 전용 챗봇에서 거래 확인 및 대응 안내를 받을 수 있습니다.
{chatbot_url}

본인 거래가 아니라면 금융회사 공식 고객센터를 통해 즉시 신고해 주시기 바랍니다.
FDShield는 이메일로 비밀번호나 인증번호를 요구하지 않습니다.
"""
    )
    return message


__all__ = [
    "EmailMessageSender",
    "FraudAlertEmailService",
    "NoOpFraudAlertEmailService",
    "SmtpEmailMessageSender",
    "build_fraud_alert_email_message",
]
