# 프론트에서 받은 GET 요청으로 서비스 코드 실행 후 대시보드 오버뷰 응답 DTO 반환

import json
from collections.abc import Iterator
from queue import Empty

from fastapi.responses import StreamingResponse

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.db import SessionDep
from app.core.common_response import ApiResponse, success_response
from app.dto.dashboard import DashboardOverviewResponse
from app.repositories.dashboard_overview import (
    DashboardOverviewRepository,
)
from app.services.dashboard.overview_service import (
    DashboardOverviewService,
)
from app.services.dashboard.dashboard_event_broker import (
    dashboard_event_broker
)


router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
)

@router.get("/events")
def stream_dashboard_events() -> StreamingResponse:
    def event_stream() -> Iterator[str]:
        subscriber_queue = dashboard_event_broker.subscribe()

        try:
            # 브라우저가 SSE 연결이 끊겼을 때 3초 후 재연결
            yield "retry: 3000\n\n"

            while True:
                try:
                    dashboard_event = subscriber_queue.get(timeout=15)
                except Empty:
                    # 연결 유지용 메시지
                    yield ": keep-alive\n\n"
                    continue

                data = json.dumps(
                    dashboard_event.data,
                    ensure_ascii=False
                )

                yield(
                    f"event: {dashboard_event.event}\n"
                    f"data: {data}\n\n"
                )

        finally:
            dashboard_event_broker.unsubscribe(subscriber_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":"no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        },
    )

@router.get(
    "/overview",
    response_model=ApiResponse[DashboardOverviewResponse],
)
def get_dashboard_overview(
    session: SessionDep,
    period_start: datetime = Query(...),
    period_end: datetime = Query(...),
) -> ApiResponse[DashboardOverviewResponse]:
    repository = DashboardOverviewRepository(session)
    service = DashboardOverviewService(repository)

    try:
        overview = service.get_overview(
            period_start=period_start,
            period_end=period_end,
        )
        return success_response(overview)

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
