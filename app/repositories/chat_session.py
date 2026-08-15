"""고객 챗봇 세션의 조회와 저장을 담당한다."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlmodel import Session, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatMessage,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.dto.chatbot import AnswerQualityVerdict


VerdictSkipReason = Literal["MAX_RETRY_EXCEEDED", "EVALUATOR_FAILED"]


class ChatSessionRepository:

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, chat_session_id: str) -> ChatSession | None:
        return self.session.get(ChatSession, chat_session_id)

    # 트렌젝션 아이디로 그에 해당하는 세션을 찾는다
    def find_by_transaction(self, transaction_id: int) -> ChatSession | None:
        """
        트렌젝션 아이디로 그에 해당하는 세션을 찾는다
        """
        return self.session.exec(
            select(ChatSession).where(
                ChatSession.transaction_id == transaction_id
            )
        ).first()

    def create_or_get(
        self,
        *,
        chat_session_id: str,
        transaction_id: int,
        top_fraud_types: list[str] | None = None,
    ) -> ChatSession:
        """
        거래에 연결된 세션이 있으면 반환하고, 없으면 새로 추가한다.
        """

        # 연결된 세션이 있는지 확인
        existing = self.find_by_transaction(transaction_id)
        # 있으면 그거 그대로 반환
        if existing is not None:
            return existing

        # 없으면 만들어낸다
        chat_session = ChatSession(
            chat_session_id=chat_session_id,
            transaction_id=transaction_id,
            top_fraud_types=(
                list(top_fraud_types) if top_fraud_types is not None else None
            ),
        )
        self.session.add(chat_session)
        return chat_session

    def update_status(
        self,
        chat_session: ChatSession,
        status: ChatSessionStatus,
    ) -> ChatSession:
        """세션 상태를 변경한다."""

        chat_session.status = status.value
        self.session.add(chat_session)
        return chat_session

    def update_question_step(
        self,
        chat_session: ChatSession,
        question_step: int,
    ) -> ChatSession:
        """턴이 끝난 뒤 현재 질문 단계를 기록한다."""

        if question_step < 0:
            raise ValueError("question_step은 0 이상이어야 합니다")
        chat_session.question_step = question_step
        self.session.add(chat_session)
        return chat_session

    def record_url_sent(
        self,
        chat_session: ChatSession,
        *,
        notified_email: str,
        email_sent_at: datetime,
    ) -> ChatSession:
        """실제 알림 수신 주소와 URL 발송 시각을 기록한다."""

        chat_session.status = ChatSessionStatus.URL_SENT.value
        chat_session.notified_email = notified_email
        chat_session.email_sent_at = email_sent_at
        self.session.add(chat_session)
        return chat_session

    def set_session_complete(
        self,
        chat_session: ChatSession,
        *,
        completed_at: datetime,
    ) -> ChatSession:
        """세션을 정상 종료하고 완료 시각을 기록한다."""

        chat_session.status = ChatSessionStatus.DONE.value
        chat_session.completed_at = completed_at
        self.session.add(chat_session)
        return chat_session

    def add_message(
        self,
        chat_session: ChatSession,
        *,
        sender_type: ChatSenderType,
        message_text: str,
    ) -> ChatMessage:
        """대화 저장 + 세션의 마지막 메시지를 갱신"""

        message = ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            sender_type=sender_type.value,
            message_text=message_text,
        )
        self.session.add(message)
        self.session.flush() # 여기서 flush를 해야 DB 오토인크리먼트값이 들어간다

        # 세션에는 마지막 메시지가 뭔지 기록하는 컬럼이 있는데 이를 변경한다
        chat_session.last_message_id = message.message_id
        self.session.add(chat_session)
        return message

    def add_answer(
        self,
        chat_session: ChatSession,
        *,
        message: ChatMessage,
        question_step: int,
        attempt_no: int,
        quality_verdict: AnswerQualityVerdict | None = None,
        verdict_skip_reason: VerdictSkipReason | None = None,
        is_adopted: bool = False,
    ) -> ChatAnswer:
        """고객 메시지에 질문별 평가·채택 메타데이터를 연결한다."""

        if not 1 <= attempt_no <= 3:
            raise ValueError("attempt_no는 1 이상 3 이하여야 합니다")
        if message.chat_session_id != chat_session.chat_session_id:
            raise ValueError("다른 세션의 메시지를 답변으로 기록할 수 없습니다")
        if message.message_id is None:
            self.session.flush()
        if message.message_id is None:
            raise ValueError("저장되지 않은 메시지는 답변으로 기록할 수 없습니다")

        answer = ChatAnswer(
            chat_session_id=chat_session.chat_session_id,
            question_step=question_step,
            attempt_no=attempt_no,
            message_id=message.message_id,
            quality_verdict=(
                quality_verdict.value if quality_verdict is not None else None
            ), # 평가 LLM 호출이 실패할 경우 None으로 들어갈 수도 있다 
            verdict_skip_reason=verdict_skip_reason,
            is_adopted=is_adopted, # 동일 질문에 대해 여러번 재질문하는 경우가 있다 그 중 최종적으로 사용하기로 한 사용자 응답
        )
        self.session.add(answer)
        return answer


__all__ = ["ChatSessionRepository"]
