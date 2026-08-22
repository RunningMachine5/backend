"""고객 챗봇 세션의 조회와 저장을 담당한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlmodel import SQLModel
from sqlmodel import Session, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatDiscriminationAction,
    ChatGuideSearchQuery,
    ChatMessage,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
)
from app.dto.chatbot import AnswerQualityVerdict
from app.dto.agent import CustomerResponseContextDTO


VerdictSkipReason = Literal["MAX_RETRY_EXCEEDED", "EVALUATOR_FAILED"]


class ChatSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, chat_session_id: str) -> ChatSession | None:
        return self.session.get(ChatSession, chat_session_id)

    def lock_for_discrimination_action(
        self,
        chat_session_id: str,
    ) -> ChatSession | None:
        """동시 퀵리플라이가 같은 질문을 두 번 진행하지 못하게 행을 잠근다."""

        return self.session.exec(
            select(ChatSession)
            .where(ChatSession.chat_session_id == chat_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def find_by_transaction(self, transaction_id: int) -> ChatSession | None:
        """거래에 연결된 채팅 세션을 조회한다."""
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
        top_fraud_type_scores: dict[str, float] | None = None,
        is_older: bool = False,
    ) -> ChatSession:
        """거래에 연결된 세션이 있으면 반환하고 없으면 생성한다."""

        existing = self.find_by_transaction(transaction_id)
        if existing is not None:
            return existing

        chat_session = ChatSession(
            chat_session_id=chat_session_id,
            transaction_id=transaction_id,
            top_fraud_types=(
                list(top_fraud_types) if top_fraud_types is not None else None
            ),
            top_fraud_type_scores=(
                dict(top_fraud_type_scores)
                if top_fraud_type_scores is not None
                else None
            ),
            is_older=is_older,
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

    def request_handoff(
        self,
        chat_session: ChatSession,
        *,
        completed_at: datetime | None = None,
    ) -> ChatSession:
        """세션을 상담사 연결 대기 상태로 변경한다."""

        chat_session.status = ChatSessionStatus.HANDOFF_REQUESTED.value
        if completed_at is not None:
            chat_session.completed_at = completed_at
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
        """메시지를 저장하고 세션의 마지막 메시지를 갱신한다."""

        message = ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            sender_type=sender_type.value,
            message_text=message_text,
        )
        self.session.add(message)
        self.session.flush()

        chat_session.last_message_id = message.message_id
        self.session.add(chat_session)
        return message

    def list_messages(self, chat_session: ChatSession) -> list[ChatMessage]:
        """세션의 대화 이력을 보낸 순서대로 조회한다(고객 화면 재접속용)."""

        return list(
            self.session.exec(
                select(ChatMessage)
                .where(
                    ChatMessage.chat_session_id == chat_session.chat_session_id
                )
                .order_by(ChatMessage.message_id)
            ).all()
        )

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
            ),
            verdict_skip_reason=verdict_skip_reason,
            is_adopted=is_adopted,
        )
        self.session.add(answer)
        return answer

    def add_guide_search_query(
        self,
        chat_session: ChatSession,
        *,
        position: int,
        title: str,
        search_query: str,
        evidence: str,
        source_answer: ChatAnswer,
    ) -> bool:
        """채택 답변에서 분해된 가이드 검색 질의를 위치별로 한 번만 저장한다."""

        normalized_title = title.strip()
        normalized_query = " ".join(search_query.split())
        if not 1 <= position <= 5:
            return False
        if not normalized_title or len(normalized_title) > 120:
            return False
        if not normalized_query or len(normalized_query) > 500:
            return False
        if not evidence or not source_answer.is_adopted:
            return False

        source_answer_id = self._source_answer_id(
            chat_session,
            source_answer,
        )
        source_message = self.session.get(ChatMessage, source_answer.message_id)
        if source_message is None or evidence not in source_message.message_text:
            return False

        return self._insert_do_nothing(
            ChatGuideSearchQuery,
            values={
                "chat_session_id": chat_session.chat_session_id,
                "position": position,
                "title": normalized_title,
                "search_query": normalized_query,
                "evidence": evidence,
                "extracted_at": datetime.now(UTC),
                "source_answer_id": source_answer_id,
            },
            index_elements=["source_answer_id", "position"],
        )

    def get_discrimination_action(
        self,
        chat_session: ChatSession,
        *,
        request_id: str,
    ) -> ChatDiscriminationAction | None:
        """같은 세션에서 이미 처리한 퀵리플라이 요청을 조회한다."""

        return self.session.exec(
            select(ChatDiscriminationAction).where(
                ChatDiscriminationAction.chat_session_id
                == chat_session.chat_session_id,
                ChatDiscriminationAction.request_id == request_id,
            )
        ).first()

    def add_discrimination_action(
        self,
        chat_session: ChatSession,
        *,
        request_id: str,
        question_id: str,
        action: str,
        response_payload: dict[str, object],
    ) -> ChatDiscriminationAction:
        """처리한 퀵리플라이와 재전송에 사용할 완료 응답을 저장한다."""

        receipt = ChatDiscriminationAction(
            chat_session_id=chat_session.chat_session_id,
            request_id=request_id,
            question_id=question_id,
            action=action,
            response_payload=dict(response_payload),
        )
        self.session.add(receipt)
        return receipt

    def get_customer_response_context(
        self,
        transaction_id: int,
    ) -> CustomerResponseContextDTO:
        """가이드 생성 시점에 사용할 고객 채택 답변을 조회한다."""

        answers = list(
            self.session.exec(
                select(ChatMessage.message_text)
                .join(ChatAnswer, ChatAnswer.message_id == ChatMessage.message_id)
                .join(
                    ChatSession,
                    ChatSession.chat_session_id == ChatAnswer.chat_session_id,
                )
                .where(
                    ChatSession.transaction_id == transaction_id,
                    ChatAnswer.is_adopted.is_(True),
                )
                .order_by(ChatAnswer.question_step, ChatAnswer.attempt_no)
            ).all()
        )
        return CustomerResponseContextDTO(
            customer_answers=answers,
            type_scores={},
        )

    def get_status_by_transaction(
        self,
        transaction_id: int,
    ) -> ChatSessionStatus | None:
        """거래에 연결된 채팅 세션의 현재 상태를 조회한다."""

        chat_session = self.find_by_transaction(transaction_id)
        if chat_session is None:
            return None
        return ChatSessionStatus(chat_session.status)

    def _source_answer_id(
        self,
        chat_session: ChatSession,
        source_answer: ChatAnswer | None,
    ) -> int | None:
        """추출 결과에 연결할 답변 id를 검증하고 반환한다."""
        if source_answer is None:
            return None
        if source_answer.chat_session_id != chat_session.chat_session_id:
            raise ValueError("다른 세션의 답변을 추출 근거로 연결할 수 없습니다")
        if source_answer.answer_id is None:
            self.session.flush()
        if source_answer.answer_id is None:
            raise ValueError("저장되지 않은 답변은 추출 근거로 연결할 수 없습니다")
        return source_answer.answer_id

    def _insert_do_nothing(
        self,
        model: type[SQLModel],
        *,
        values: dict[str, object],
        index_elements: list[str],
    ) -> bool:
        """고유 키가 이미 존재하면 삽입하지 않는다."""
        statement = (
            postgresql_insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=index_elements)
        )
        result = self.session.exec(statement)
        return result.rowcount == 1


__all__ = ["ChatSessionRepository"]
