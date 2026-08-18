# 처리 목록·상세 화면 전용의 읽기 DB 접근 계층.

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.data.model.agent import AgentCase, AgentReview
from app.data.model.chatbot import ChatMessage, ChatSession
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel


@dataclass(frozen=True)
class CaseListRow:
    # 처리 목록 한 행을 조립하기 전의 DB 원본 묶음.

    transaction: Transaction
    prediction: MLPredictionResult | None
    score_result: FraudTypeScoreResult | None
    agent_case: AgentCase | None
    review: AgentReview | None
    label: TransactionLabel | None


class CaseQueryRepository:
    # 프론트 화면 조회에만 사용하는 읽기 전용 Repository.

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_suspicious_cases(
        self,
        *,
        transaction_id: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        customer_id: str | None = None,
        ip_address: str | None = None,
        recipient_account_number: str | None = None,
        min_amount: int | None = None,
        max_amount: int | None = None,
        risk_grades: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[CaseListRow], int]:
        """ML이 의심 거래로 판정한 처리 대상 목록과 전체 건수를 조회한다."""

        statement = (
            select(
                Transaction,
                MLPredictionResult,
                FraudTypeScoreResult,
                AgentCase,
                AgentReview,
                TransactionLabel,
            )
            .join(
                MLPredictionResult,
                MLPredictionResult.transaction_id == Transaction.id,
            )
            .outerjoin(
                FraudTypeScoreResult,
                FraudTypeScoreResult.transaction_id == Transaction.id,
            )
            .outerjoin(AgentCase, AgentCase.transaction_id == Transaction.id)
            .outerjoin(AgentReview, AgentReview.case_id == AgentCase.case_id)
            .outerjoin(
                TransactionLabel,
                TransactionLabel.transaction_id == Transaction.id,
            )
            .where(MLPredictionResult.predict_result.is_(True))
        )

        if transaction_id is not None:
            statement = statement.where(Transaction.id == transaction_id)
        if period_start is not None:
            statement = statement.where(Transaction.transaction_datetime >= period_start)
        if period_end is not None:
            statement = statement.where(Transaction.transaction_datetime < period_end)
        if customer_id:
            statement = statement.where(Transaction.customer_id == customer_id)
        if ip_address:
            statement = statement.where(Transaction.ip_address == ip_address)
        if recipient_account_number:
            statement = statement.where(
                Transaction.recipient_account_number == recipient_account_number
            )
        if min_amount is not None:
            statement = statement.where(Transaction.transaction_amount >= min_amount)
        if max_amount is not None:
            statement = statement.where(Transaction.transaction_amount <= max_amount)
        if risk_grades:
            statement = statement.where(AgentCase.risk_grade.in_(risk_grades))

        count_statement = select(func.count()).select_from(statement.subquery())
        total_count = self.session.exec(count_statement).one()
        rows = self.session.exec(
            statement.order_by(
                func.coalesce(AgentCase.risk_score, -1).desc(),
                Transaction.transaction_datetime.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return (
            [
                CaseListRow(
                    transaction=transaction,
                    prediction=prediction,
                    score_result=score_result,
                    agent_case=agent_case,
                    review=review,
                    label=label,
                )
                for transaction, prediction, score_result, agent_case, review, label in rows
            ],
            total_count,
        )

    def get_transaction(self, transaction_id: int) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def get_latest_prediction(self, transaction_id: int) -> MLPredictionResult | None:
        return self.session.exec(
            select(MLPredictionResult)
            .where(MLPredictionResult.transaction_id == transaction_id)
            .order_by(MLPredictionResult.created_at.desc(), MLPredictionResult.id.desc())
        ).first()

    def get_score_result(self, transaction_id: int) -> FraudTypeScoreResult | None:
        return self.session.exec(
            select(FraudTypeScoreResult).where(
                FraudTypeScoreResult.transaction_id == transaction_id
            )
        ).first()

    def get_agent_case(self, transaction_id: int) -> AgentCase | None:
        return self.session.exec(
            select(AgentCase).where(AgentCase.transaction_id == transaction_id)
        ).first()

    def get_review(self, case_id: str | None) -> AgentReview | None:
        return self.session.get(AgentReview, case_id) if case_id else None

    def get_chat_session(self, transaction_id: int) -> ChatSession | None:
        return self.session.exec(
            select(ChatSession).where(ChatSession.transaction_id == transaction_id)
        ).first()

    def list_chat_messages(self, chat_session_id: str) -> list[ChatMessage]:
        return list(
            self.session.exec(
                select(ChatMessage)
                .where(ChatMessage.chat_session_id == chat_session_id)
                .order_by(ChatMessage.sent_at, ChatMessage.message_id)
            ).all()
        )
