from fastapi import APIRouter, HTTPException

from app.core.db import SessionDep
from app.dto.dashboard_insight import (
    DashboardInsightChartSpec,
    DashboardInsightGenerateRequest,
    DashboardInsightResponse,
)
from app.pipelines.dashboard_insight_pipeline import (
    DashboardInsightPipeline,
)
from app.repositories.dashboard_insight import (
    DashboardInsightRepository,
)

router = APIRouter(
    prefix="/api/dashboard/insights",
    tags=["dashboard-insights"],
)

@router.post(
    "/generate",
    response_model=DashboardInsightResponse,
)
def generate_dashboard_insight(
    request: DashboardInsightGenerateRequest,
    session: SessionDep,
) -> DashboardInsightResponse:
    pipeline = DashboardInsightPipeline(session)

    try:
        return pipeline.run(request)

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.get(
    "/latest",
    response_model=DashboardInsightResponse,
)
def get_latest_dashboard_insight(
    session: SessionDep,
) -> DashboardInsightResponse:
    repository = DashboardInsightRepository(session)
    insight = repository.get_latest()

    if insight is None:
        raise HTTPException(
            status_code=404,
            detail="생성된 Dashboard Insight가 없습니다.",
        )

    return DashboardInsightResponse(
        insight_id=insight.insight_id,
        title=insight.title,
        summary=insight.summary or "",
        chart_spec=DashboardInsightChartSpec.model_validate(
            insight.chart_spec
        ),
        created_at=insight.created_at,
    )