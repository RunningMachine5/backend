from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from app.data.model.agent import AgentCase, AgentReview

class CaseReviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_agent_case(
        self,
        case_id: str,
    ) -> AgentCase | None:
        return self.session.get(AgentCase, case_id)

    def get_review(
        self,
        case_id: str
    ) -> AgentReview | None:
        return self.session.get(AgentReview, case_id)

    def upsert_review(
        self,
        *,
        case_id: str,
        reviewer_id: str,
        decision: str,
        confirmed_fraud_type: str | None,
        performed_actions: list[dict[str, Any]],
        checklist_results: list[dict[str, Any]],
        resolution_summary: str | None,
    ) -> AgentReview:
        review = self.get_review(case_id)

        if review is None:
            review = AgentReview(
                case_id=case_id,
                reviewer_id=reviewer_id,
                decision=decision,
                confirmed_fraud_type=confirmed_fraud_type,
                performed_actions=performed_actions,
                checklist_results=checklist_results,
                resolution_summary=resolution_summary,
                reviewed_at=datetime.now(UTC)
            )
            self.session.add(review)

        else:
            review.reviewer_id = reviewer_id
            review.decision = decision
            review.confirmed_fraud_type = confirmed_fraud_type
            review.performed_actions = performed_actions
            review.checklist_results = checklist_results
            review.resolution_summary = resolution_summary
            review.reviewed_at = datetime.now(UTC)

            self.session.add(review)

        self.session.commit()
        self.session.refresh(review)

        return review