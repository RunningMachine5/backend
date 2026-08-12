"""Agent 사건 생성과 상태 변경에 필요한 DB 접근을 담당한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.data.model.agent import AgentCase
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudTypeScoreResult,
)
from app.data.model.transaction import Transaction
from app.domain.agent_status import AgentExecutionStatus


class AgentCaseRepository:
    """Agent 서비스가 사용하는 조회와 저장 연산을 한곳에 모은다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def get_score_result(
        self,
        fraud_type_score_result_id: int,
    ) -> FraudTypeScoreResult | None:
        return self.session.get(
            FraudTypeScoreResult,
            fraud_type_score_result_id,
        )

    def get_case(self, case_id: str) -> AgentCase | None:
        return self.session.get(AgentCase, case_id)

    def find_case_by_transaction(self, transaction_id: str) -> AgentCase | None:
        return self.session.exec(
            select(AgentCase).where(AgentCase.transaction_id == transaction_id)
        ).first()

    def find_case_by_score_result(
        self,
        fraud_type_score_result_id: int,
    ) -> AgentCase | None:
        return self.session.exec(
            select(AgentCase).where(
                AgentCase.fraud_type_score_result_id
                == fraud_type_score_result_id
            )
        ).first()

    def list_rules(self, rule_set_id: int) -> list[FraudRule]:
        return list(
            self.session.exec(
                select(FraudRule)
                .where(FraudRule.rule_set_id == rule_set_id)
                .order_by(FraudRule.sort_order, FraudRule.id)
            ).all()
        )

    def list_components(self, rule_ids: list[int]) -> list[FraudRuleComponent]:
        if not rule_ids:
            return []
        return list(
            self.session.exec(
                select(FraudRuleComponent)
                .where(FraudRuleComponent.rule_id.in_(rule_ids))
                .order_by(FraudRuleComponent.sort_order, FraudRuleComponent.id)
            ).all()
        )

    def add_processing_case(
        self,
        *,
        case_id: str,
        transaction_id: str,
        fraud_type_score_result_id: int,
        risk_score: int,
        risk_grade: str,
    ) -> AgentCase:
        """실행 직전의 사건을 PROCESSING 상태로 세션에 추가한다."""

        agent_case = AgentCase(
            case_id=case_id,
            transaction_id=transaction_id,
            fraud_type_score_result_id=fraud_type_score_result_id,
            execution_status=AgentExecutionStatus.PROCESSING.value,
            risk_score=risk_score,
            risk_grade=risk_grade,
            generation_metadata={},
        )
        self.session.add(agent_case)
        return agent_case

    def mark_completed(
        self,
        agent_case: AgentCase,
        *,
        investigation_result: dict[str, Any] | None,
        best_similar_case_id: str | None,
        similar_case_results: list[dict[str, Any]],
        response_result: dict[str, Any],
        generation_metadata: dict[str, Any],
        completed_at: datetime,
    ) -> AgentCase:
        """Agent 산출물과 함께 사건을 COMPLETED 상태로 변경한다."""

        agent_case.execution_status = AgentExecutionStatus.COMPLETED.value
        agent_case.failure_reason = None
        agent_case.investigation_result = investigation_result
        agent_case.best_similar_case_id = best_similar_case_id
        agent_case.similar_case_results = similar_case_results
        agent_case.response_result = response_result
        agent_case.generation_metadata = generation_metadata
        agent_case.completed_at = completed_at
        self.session.add(agent_case)
        return agent_case

    def mark_failed(
        self,
        agent_case: AgentCase,
        *,
        failure_reason: str,
        generation_metadata: dict[str, Any],
        completed_at: datetime,
    ) -> AgentCase:
        """실패 원인을 보존하고 사건을 FAILED 상태로 변경한다."""

        agent_case.execution_status = AgentExecutionStatus.FAILED.value
        agent_case.failure_reason = failure_reason
        agent_case.generation_metadata = generation_metadata
        # completed_at은 성공 시각이 아니라 Agent 실행이 종료된 시각으로 사용한다.
        agent_case.completed_at = completed_at
        self.session.add(agent_case)
        return agent_case

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def refresh(self, agent_case: AgentCase) -> None:
        self.session.refresh(agent_case)


__all__ = ["AgentCaseRepository"]
