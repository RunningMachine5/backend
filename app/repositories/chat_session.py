"""고객 챗봇 세션의 조회와 저장을 담당한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlmodel import SQLModel
from sqlmodel import Session, select

from app.data.model.chatbot import (
    ChatAnswer,
    ChatCustomerAction,
    ChatFraudCircumstance,
    ChatMessage,
    ChatSenderType,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.domain.customer_action_codes import FINAL_CUSTOMER_ACTION_CODES
from app.domain.fraud_circumstance_codes import FINAL_FRAUD_CIRCUMSTANCE_CODES
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

    def add_customer_action(
        self,
        chat_session: ChatSession,
        *,
        action_code: str,
        evidence: str,
        source_answer: ChatAnswer | None = None,
    ) -> bool:
        """같은 세션에서 같은 고객행동 코드는 한 번만 저장"""

        if action_code not in FINAL_CUSTOMER_ACTION_CODES:
            return False

        return self._insert_do_nothing(
            ChatCustomerAction,
            values={
                "chat_session_id": chat_session.chat_session_id,
                "action_code": action_code,
                "evidence": evidence,
                "extracted_at": datetime.now(UTC),
                "source_answer_id": self._source_answer_id(
                    chat_session,
                    source_answer,
                ),
            },
            index_elements=["chat_session_id", "action_code"],
        )

    def add_fraud_circumstance(
        self,
        chat_session: ChatSession,
        *,
        circumstance_code: str,
        evidence: str,
        source_answer: ChatAnswer | None = None,
    ) -> bool:
        """같은 세션에서 같은 사기 정황 코드는 한 번만 저장"""

        if circumstance_code not in FINAL_FRAUD_CIRCUMSTANCE_CODES:
            return False

        return self._insert_do_nothing(
            ChatFraudCircumstance,
            values={
                "chat_session_id": chat_session.chat_session_id,
                "circumstance_code": circumstance_code,
                "evidence": evidence,
                "extracted_at": datetime.now(UTC),
                "source_answer_id": self._source_answer_id(
                    chat_session,
                    source_answer,
                ),
            },
            index_elements=["chat_session_id", "circumstance_code"],
        )

    def list_fraud_circumstances(
        self,
        chat_session: ChatSession,
    ) -> list[ChatFraudCircumstance]:
        """
        세션에서 추출된 사기 정황을 모두 조회한다.
        채점을 진행할때 사용
        """

        return list(
            self.session.exec(
                select(ChatFraudCircumstance)
                .where(
                    ChatFraudCircumstance.chat_session_id
                    == chat_session.chat_session_id
                )
                .order_by(ChatFraudCircumstance.circumstance_id)
            ).all()
        )

    def add_fraud_type_scores(
        self,
        chat_session: ChatSession,
        *,
        type_scores: dict[str, float],
    ) -> bool:
        """채팅으로 얻어진 사기 정보를 거래당 한 번만 저장한다."""

        return self._insert_do_nothing(
            FraudTypeScoreAfterChat,
            values={
                "transaction_id": chat_session.transaction_id,
                "chat_session_id": chat_session.chat_session_id,
                "type_scores": dict(type_scores),
                "scored_at": datetime.now(UTC),
            },
            # 이미 저장되어 있으면 저장하지 않는다
            index_elements=["transaction_id"],
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
        """추출된 행동·사기 정황이 어떤 고객 답변에서 나온 것인지 연결할 answer_id를 준비"""
        if source_answer is None: # source_answer가 없으면 None 반환
            return None
        if source_answer.chat_session_id != chat_session.chat_session_id: # 다른 채팅 세션의 답변이면 오류 발생
            raise ValueError("다른 세션의 답변을 추출 근거로 연결할 수 없습니다")
        if source_answer.answer_id is None: # 아직 DB에서 answer_id가 발급되지 않았다면 flush() 실행
            self.session.flush()
        if source_answer.answer_id is None: # 그럼에도 없다면
            raise ValueError("저장되지 않은 답변은 추출 근거로 연결할 수 없습니다")
        return source_answer.answer_id

    def _insert_do_nothing(
        self,
        model: type[SQLModel],
        *,
        values: dict[str, object],
        index_elements: list[str],
    ) -> bool:
        """중복 삽입 안되게 하는 로직"""
        statement = (
            postgresql_insert(model)
            .values(**values)
            # index_elements 컬럼 조합이 이미 존재하면 예외를 내지 않고 INSERT를 건너뜀
            .on_conflict_do_nothing(index_elements=index_elements)
        )
        result = self.session.exec(statement)
        return result.rowcount == 1


__all__ = ["ChatSessionRepository"]
