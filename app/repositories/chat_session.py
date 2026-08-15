"""고객 챗봇 세션의 조회와 저장을 담당한다."""

from __future__ import annotations

from sqlmodel import Session, select

from app.data.model.chatbot import ChatSession


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


__all__ = ["ChatSessionRepository"]
