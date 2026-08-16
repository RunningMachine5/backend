"""Agent 이상거래 안내 메일과 챗봇 세션 생성을 한 번에 처리한다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlmodel import Session

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.dto.agent import FraudAlertEmailCommand
from app.repositories.chat_session import ChatSessionRepository
from app.services.agent.email_sender import FraudAlertEmailService
from app.services.chatbot.session_creator import ChatSessionCreator
from app.services.chatbot.session_url_mailer import build_chat_url


logger = logging.getLogger(__name__)


class ChatSessionAlertNotifier:
    """세션을 생성하고 Agent 안내 메일 한 통에 접속 URL을 넣는다.
    """

    def __init__(
        self,
        *,
        session: Session,
        email_notifier: FraudAlertEmailService,
        session_creator: ChatSessionCreator | None = None,
        repository: ChatSessionRepository | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.email_notifier = email_notifier
        self.session_creator = session_creator or ChatSessionCreator(session)
        self.repository = repository or ChatSessionRepository(session)
        self.now_factory = now_factory or (lambda: datetime.now(UTC))

    def send(self, command: FraudAlertEmailCommand) -> None:
        """신규 세션에 대해서만 통합 안내 메일을 보내고 상태를 기록한다."""

        # ChatSessionCreator 객체를 만들어서

        # 트랜젝션 id 에서 상위 2개 사기 유형 정보를 가져온다
        creation = self.session_creator.create(
            transaction_id=command.transaction_id,
            top_fraud_types=[
                command.primary_suspected_type,
                command.secondary_suspected_type,
            ],
            send_notification=False,
        )
        # 기존 챗봇 세션이 있으면 이메일을 다시 보내지 않고 종료
        if not creation.created:
            return
        # 생선된 세션 정보를 알아야 메일에 chatbot_url 을 넣어줄 수 있다
        chat_session = creation.chat_session
        notified_email = creation.notified_email
        if notified_email is None:
            self._record_failed(chat_session, None)
            raise RuntimeError("이상거래 안내 이메일 수신 주소가 없습니다")

        try:
            # 실제로 메일 보내는 부분
            email_sent = self.email_notifier.send(
                command,
                chatbot_url=build_chat_url(chat_session.chat_session_id),
            )
        except Exception:
            self._record_failed(chat_session, notified_email)
            raise

        # 이메일 만들기 위한 거래 ,고객 정보를 찾지 못하면 실행된다
        if not email_sent:
            self._record_failed(chat_session, notified_email)
            logger.warning(
                "이상거래 안내 이메일 문맥을 찾지 못했습니다: transaction_id=%s",
                command.transaction_id,
            )
            return

        self.repository.record_url_sent(
            chat_session,
            notified_email=notified_email,
            email_sent_at=self.now_factory(),
        )

    # 이메일 전송 실패 or 이메일 만들기 위한 정보 찾기 실패하면 ChatSessionStatus.FAILED 로 갱신한다
    def _record_failed(
        self,
        chat_session: ChatSession,
        notified_email: str | None,
    ) -> None:
        """발송 실패 상태와 시도한 수신 주소를 같은 트랜잭션에 남긴다."""

        chat_session.notified_email = notified_email
        self.repository.update_status(chat_session, ChatSessionStatus.FAILED)


__all__ = ["ChatSessionAlertNotifier"]
