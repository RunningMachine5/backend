# 대시보드 에이전트가 하는 분석에 필요한 db 데이터를 조회한다.
# 생성된 Insight를 agent_dashboard_insights에 저장한다.

from datetime import datetime
from uuid import uuid4

from sqlmodel import Session, select

from app.data.model.agent import (
    AgentCase,
    AgentDashboardInsight,
)
from app.data.model.derived_features import DerivedFeatures
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.data.model.transaction import Transaction
from app.dto.dashboard_insight import (
    DashboardInsightChartSpec,
    DashboardInsightSourceRecord,
)

class DashboardInsightRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # 지정된 기간의 이상거래 분석 데이터를 조회함
    def get_period_records(
            self, 
            period_start: datetime, 
            period_end: datetime
        ) -> list[DashboardInsightSourceRecord]:

        statement = (
            select(
                Transaction,
                FraudTypeScoreResult,
                AgentCase,
                DerivedFeatures,
            )
            .join(
                FraudTypeScoreResult,
                FraudTypeScoreResult.transaction_id
                == Transaction.id,
            )
            .outerjoin(
                AgentCase,
                AgentCase.transaction_id == Transaction.id,
            )
            .outerjoin(
                DerivedFeatures,
                DerivedFeatures.id
                == Transaction.id,
            )
            .where(
                Transaction.transaction_datetime >= period_start,
                Transaction.transaction_datetime < period_end,
                FraudTypeScoreResult.rule_filter_status
                == "APPLIED",
            )
            .order_by(
                Transaction.transaction_datetime,
                Transaction.id,
            )
        )

        rows = self.session.exec(statement).all()

        return [
            self._to_source_record(
                transaction=transaction,
                score_result=score_result,
                agent_case=agent_case,
                derived=derived,
            )
            for transaction, score_result, agent_case, derived in rows
        ]

    def add(
            self,
            *,
            title: str,
            summary: str,
            chart_spec: DashboardInsightChartSpec,
    )-> AgentDashboardInsight:
        # 생성된 insight를 세션에 추가함
        insight = AgentDashboardInsight(
            insight_id=f"INSIGHT-{uuid4().hex}",
            title=title,
            summary=summary,
            chart_spec=chart_spec.model_dump(mode="json")
        )

        self.session.add(insight)
        return insight

    def get_latest(self) -> AgentDashboardInsight | None:
        # 가장 최근에 생성된 insight를 조회함

        statement = (
            select(AgentDashboardInsight)
            .order_by(AgentDashboardInsight.created_at.desc())
            .limit(1)
        )

        return self.session.exec(statement).first()

    @staticmethod
    def _to_source_record(
        *,
        transaction: Transaction,
        score_result: FraudTypeScoreResult,
        agent_case: AgentCase | None,
        derived: DerivedFeatures | None,
    ) -> DashboardInsightSourceRecord:
        features = {
            "channel": transaction.channel,
            "location": transaction.location,
            "vpn_indicator": transaction.vpn_indicator,
            "rooting_jailbreak_indicator": (
                transaction.rooting_jailbreak_indicator
            ),
            "mobile_roaming_indicator": (
                transaction.mobile_roaming_indicator
            ),
            "another_person_account": (
                transaction.another_person_account
            ),
            "malicious_terminal_behavior": any(
                (
                    transaction.flag_terminal_malicious_behavior_1,
                    transaction.flag_terminal_malicious_behavior_2,
                    transaction.flag_terminal_malicious_behavior_3,
                    transaction.flag_terminal_malicious_behavior_5,
                    transaction.flag_terminal_malicious_behavior_6,
                )
            ),
        }

        if derived is not None:
            features.update(
                {
                    "unused_terminal_status": (
                        derived.unused_terminal_status
                    ),
                    "unused_account_status": (
                        derived.unused_account_status
                    ),
                    "large_deposit": (
                        derived.flag_deposit_more_than_tenMillion
                    ),
                    "new_recipient": (
                        derived.number_of_transaction_with_the_account
                        <= 1
                    ),
                    "authentication_changed": any(
                        (
                            derived.flag_change_of_authentication_1,
                            derived.flag_change_of_authentication_2,
                            derived.flag_change_of_authentication_3,
                            derived.flag_change_of_authentication_4,
                        )
                    ),
                    "recipient_account_suspended": (
                        derived.recipient_account_suspend_status
                    ),
                }
            )

        return DashboardInsightSourceRecord(
            case_id = (agent_case.case_id
                       if agent_case is not None
                       else transaction.id
            ),
            account_number=transaction.source_account_number,
            transaction_time=transaction.transaction_datetime,
            transaction_amount = transaction.transaction_amount,

            risk_score = (agent_case.risk_score
                          if agent_case is not None
                          else None
            ),
            risk_grade = (agent_case.risk_grade
                          if agent_case is not None
                          else None),

            primary_fraud_type = score_result.primary_fraud_type,
            type_scores = score_result.type_scores or {},
            matched_components = score_result.matched_components or {},
            transaction_features = features,
        )
