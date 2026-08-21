from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.common_response import ApiResponse, success_response
from app.core.db import SessionDep
from app.dto.dashboard import CaseDetailResponse, CaseListResponse
from app.dto.case_review import CaseReviewResponse, CaseReviewUpsertRequest
from app.repositories.case_review import CaseReviewRepository
from app.services.dashboard.case_review_service import CaseNotFoundError, CaseReviewService
from app.repositories.case_query import CaseQueryRepository
from app.services.dashboard.case_query_service import CaseQueryService

router = APIRouter(
    prefix="/api",
    tags=["cases"]
)

def get_case_query_service(
    session: SessionDep
)-> CaseQueryService:
    repository = CaseQueryRepository(session)
    return CaseQueryService(repository)

def get_case_review_service(
    session: SessionDep
) -> CaseReviewService:
    repository = CaseReviewRepository(session)
    return CaseReviewService(repository)

@router.put(
    "/cases/{case_id}/review",
    response_model=ApiResponse[CaseReviewResponse]
)

def save_case_review(
    case_id: str,
    request: CaseReviewUpsertRequest,
    session: SessionDep,
) -> ApiResponse[CaseReviewResponse]:
    service = get_case_review_service(session)

    try:
        review = service.save_review(
            case_id=case_id,
            request=request
        )
    except CaseNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        ) from error

    return success_response(review)

@router.get(
    "/cases",
    response_model=ApiResponse[CaseListResponse]
)

def get_cases(
    session: SessionDep,
    transaction_id: int | None = Query(default=None, ge=1),
    period_start: datetime | None = Query(default=None),
    period_end: datetime | None = Query(default=None),
    customer_id: int | None = Query(default=None, ge=1),
    ip_address: str | None = Query(default=None),
    recipient_account_number: str | None = Query(default=None),
    min_amount: int | None = Query(default=None, ge=0),
    max_amount: int | None = Query(default=None, ge=0),
    risk_grades: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> ApiResponse[CaseListResponse]:
    service = get_case_query_service(session)

    try:
        items, total_count = service.list_cases(
            transaction_id=transaction_id,
            period_start=period_start,
            period_end=period_end,
            customer_id=customer_id,
            ip_address=ip_address,
            recipient_account_number=recipient_account_number,
            min_amount=min_amount,
            max_amount=max_amount,
            risk_grades=risk_grades,
            page=page,
            page_size=page_size,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error)
        ) from error

    return success_response(
        CaseListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total_count=total_count
        )
    )

@router.get(
    "/transactions/{transaction_id}/detail",
    response_model=ApiResponse[CaseDetailResponse]
)
def get_transaction_detail(
    transaction_id: int,
    session: SessionDep
) -> ApiResponse[CaseDetailResponse]:
    service = get_case_query_service(session)
    detail = service.get_case_detail(transaction_id)

    if detail is None:
        raise HTTPException(
            status_code=404,
            detail="거래를 찾을 수 없음"
        )

    return success_response(detail)
