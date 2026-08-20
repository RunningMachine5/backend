from app.dto.case_review import (
    CaseReviewResponse,
    CaseReviewUpsertRequest,
    ReviewDecision
)
from app.repositories.case_review import CaseReviewRepository

SYSTEM_REVIEWER_ID = "FDS_OPERATOR"

class CaseNotFoundError(Exception):
    pass

class CaseReviewService:
    def __init__(
        self,
        repository: CaseReviewRepository,
    ) -> None:
        self.repository = repository

    def save_review(
        self,
        *,
        case_id: str,
        request: CaseReviewUpsertRequest
    ) -> CaseReviewResponse:
        agent_case = self.repository.get_agent_case(case_id)

        if agent_case is None:
            raise CaseNotFoundError("사건을 찾을 수 없습니다.")

        review = self.repository.upsert_review(
            case_id=case_id,
            reviewer_id=SYSTEM_REVIEWER_ID,
            decision=request.decision.value,
            confirmed_fraud_type=request.confirmed_fraud_type,
            performed_actions=[
                action.model_dump()
                for action in request.performed_actions
            ],
            checklist_results=[
                item.model_dump()
                for item in request.checklist_results
            ],
            resolution_summary=request.resolution_summary
        )

        return CaseReviewResponse(
            case_id=review.case_id,
            reviewer_id=review.reviewer_id,
            decision=ReviewDecision(review.decision),
            confirmed_fraud_type=review.confirmed_fraud_type,
            performed_actions=review.performed_actions or [],
            checklist_results=review.checklist_results or [],
            resolution_summary=review.resolution_summary,
            reviewed_at=review.reviewed_at
        )
