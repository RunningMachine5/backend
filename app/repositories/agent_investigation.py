"""유사 완료 사건 조사 Tool에 필요한 읽기 전용 DB 조회."""

from sqlmodel import Session, select

from app.data.model.agent import AgentCase, AgentReview
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.domain.agent_status import AgentExecutionStatus


class AgentInvestigationRepository:
    """담당자 검토까지 끝난 사건과 거래 문맥을 조회한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_resolved_cases(
        self,
        *,
        current_case_id: str,
        candidate_fraud_types: tuple[str, ...],
    ) -> list[tuple[AgentCase, FraudTypeScoreResult, AgentReview]]:
        statement = (
            select(AgentCase, FraudTypeScoreResult, AgentReview)
            .join(
                FraudTypeScoreResult,
                FraudTypeScoreResult.id == AgentCase.fraud_type_score_result_id,
            )
            .join(AgentReview, AgentReview.case_id == AgentCase.case_id)
            .where(
                AgentCase.case_id != current_case_id,
                AgentCase.execution_status == AgentExecutionStatus.COMPLETED.value,
                AgentReview.decision == "CONFIRMED_FRAUD",
                AgentReview.confirmed_fraud_type.in_(candidate_fraud_types),
            )
        )
        return list(self.session.exec(statement).all())

    def get_resolved_review(
        self,
        case_id: str,
    ) -> AgentReview | None:
        statement = (
            select(AgentReview)
            .join(AgentCase, AgentCase.case_id == AgentReview.case_id)
            .where(
                AgentCase.case_id == case_id,
                AgentCase.execution_status == AgentExecutionStatus.COMPLETED.value,
            )
        )
        return self.session.exec(statement).first()


__all__ = ["AgentInvestigationRepository"]
