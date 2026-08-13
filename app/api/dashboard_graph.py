# 프론트에서 받은 GET 요청으로 서비스 코드 실행 후 대시보드 오버뷰 응답 DTO 반환


from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.db import SessionDep
from app.dto.dashboard import DashboardOverviewResponse
from app.repositories.dashboard_overview import (
    DashboardOverviewRepository,
)
from app.services.dashboard.overview_service import (
    DashboardOverviewService,
)


router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
)

@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
)
def get_dashboard_overview(
    session: SessionDep,
    period_start: datetime = Query(...),
    period_end: datetime = Query(...),
) -> DashboardOverviewResponse:
    repository = DashboardOverviewRepository(session)
    service = DashboardOverviewService(repository)

    try:
        return service.get_overview(
            period_start=period_start,
            period_end=period_end,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc