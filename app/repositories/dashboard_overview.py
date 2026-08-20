# DB에서 overview에 필요할 원본 집계 대상 데이터 조회함
# 전체 거래 수(선택 기간 내), 의심 거래 목록, 최신 AI insight 가져옴

# transactions : 
# fraud_type_score_results, 
# agent_cases optional, 
# ml_prediction_results optional

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select
from sqlalchemy import func

from app.data.model.transaction import Transaction
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.agent import AgentCase

# 프론트 응답 DTO 아니고, 내부 전달용 row 객체
@dataclass(frozen=True)
class DashboardSuspiciousRow:
    transaction_id: int
    transaction_datetime: datetime
    transaction_amount: int
    channel: str
    risk_score: int | None
    risk_grade: str | None
    primary_fraud_type: str | None


class DashboardOverviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # 전체 거래 수 세는 쿼리
    def count_transactions(
            self,
            period_start: datetime,
            period_end: datetime
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.transaction_datetime >= period_start,
                Transaction.transaction_datetime < period_end
            )
        )
        return self.session.exec(statement).one()

    # 룰 분석 완료 수
    def count_rule_analysis_completed(
            self,
            period_start: datetime,
            period_end: datetime
    ) -> int:
        statement = (
            select(func.count())
            .select_from(FraudTypeScoreResult)
            .join(
                Transaction,
                Transaction.id
                == FraudTypeScoreResult.transaction_id
            )
            .where(
                Transaction.transaction_datetime >= period_start,
                Transaction.transaction_datetime < period_end
            )
        )

        return self.session.exec(statement).one()

    # 의심 거래 목록 조회 함수
    def list_suspicious_rows(
            self,
            period_start: datetime,
            period_end: datetime
    ) -> list[DashboardSuspiciousRow]:
        
        statement = (
            select(
                Transaction,
                FraudTypeScoreResult,
                AgentCase,
            )
            .join(
                FraudTypeScoreResult,
                FraudTypeScoreResult.transaction_id
                == Transaction.id,
            )
            .outerjoin(
                AgentCase,
                AgentCase.transaction_id
                == Transaction.id,
            )
            .where(
                Transaction.transaction_datetime >= period_start,
                Transaction.transaction_datetime < period_end,
                FraudTypeScoreResult.rule_filter_status == "APPLIED",
            )
            .order_by(
                Transaction.transaction_datetime,
                Transaction.id,
            )
        )

        rows = self.session.exec(statement).all()

        # 이거 왤케 김?
        return[
            DashboardSuspiciousRow(
            transaction_id=transaction.id,
            transaction_datetime=transaction.transaction_datetime,
            transaction_amount=transaction.transaction_amount,
            channel=transaction.channel,
            risk_score=(
                agent_case.risk_score
                if agent_case is not None
                else None
            ),
            risk_grade=(
                agent_case.risk_grade
                if agent_case is not None
                else None
            ),
            primary_fraud_type=score_result.primary_fraud_type,
        )
            for transaction, score_result, agent_case in rows
        ]
