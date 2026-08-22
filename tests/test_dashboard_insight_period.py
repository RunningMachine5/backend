import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

from app.data.model.agent import AgentDashboardInsight
from app.dto.dashboard_insight import DashboardInsightGenerateRequest
from app.pipelines.dashboard_insight_pipeline import DashboardInsightPipeline
from app.repositories.dashboard_insight import DashboardInsightRepository
from app.services.dashboard.overview_service import DashboardOverviewService


def _chart_spec(period_start: datetime, period_end: datetime) -> dict:
    duration = period_end - period_start
    return {
        "chart_type": "GROUPED_HORIZONTAL_BAR",
        "period": {
            "current_start": period_start.isoformat(),
            "current_end": period_end.isoformat(),
            "comparison_start": (period_start - duration).isoformat(),
            "comparison_end": period_start.isoformat(),
        },
        "x_axis": {"label": "관련 사건 수", "unit": "건"},
        "y_axis": {"label": "이상징후 원인 조합", "unit": None},
        "series": [],
        "items": [],
    }


def _insight(
    insight_id: str,
    period_start: datetime,
    period_end: datetime,
) -> AgentDashboardInsight:
    return AgentDashboardInsight(
        insight_id=insight_id,
        title="기간별 분석",
        summary="요약",
        chart_spec=_chart_spec(period_start, period_end),
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


class DashboardInsightPeriodTest(unittest.TestCase):
    def setUp(self) -> None:
        self.period_start = datetime(2026, 8, 15, tzinfo=UTC)
        self.period_end = datetime(2026, 8, 20, tzinfo=UTC)

    def test_repository_returns_latest_insight_for_same_period(self) -> None:
        session = Mock()
        different_period = _insight(
            "INSIGHT-DIFFERENT",
            self.period_start - timedelta(days=1),
            self.period_end - timedelta(days=1),
        )

        korea_timezone = timezone(timedelta(hours=9))
        same_period = _insight(
            "INSIGHT-SAME",
            self.period_start.astimezone(korea_timezone),
            self.period_end.astimezone(korea_timezone),
        )
        session.exec.return_value.all.return_value = [
            different_period,
            same_period,
        ]

        result = DashboardInsightRepository(session).get_latest_for_period(
            self.period_start,
            self.period_end,
        )

        self.assertIs(result, same_period)

    def test_pipeline_reuses_existing_insight_without_running_agent(self) -> None:
        session = Mock()
        pipeline = DashboardInsightPipeline(session)
        pipeline.repository = Mock()
        pipeline._build_agent = Mock()
        pipeline.repository.get_latest_for_period.return_value = _insight(
            "INSIGHT-EXISTING",
            self.period_start,
            self.period_end,
        )

        response = pipeline.run(
            DashboardInsightGenerateRequest(
                period_start=self.period_start,
                period_end=self.period_end,
            )
        )

        self.assertEqual(response.insight_id, "INSIGHT-EXISTING")
        pipeline._build_agent.assert_not_called()
        session.commit.assert_not_called()

    def test_overview_looks_up_insight_for_requested_period(self) -> None:
        overview_repository = Mock()
        overview_repository.count_transactions.return_value = 0
        overview_repository.count_rule_analysis_completed.return_value = 0
        overview_repository.count_completed_cases.return_value = 0
        overview_repository.list_suspicious_rows.return_value = []
        insight_repository = Mock()
        insight_repository.get_latest_for_period.return_value = None

        response = DashboardOverviewService(
            overview_repository,
            insight_repository,
        ).get_overview(
            self.period_start,
            self.period_end,
        )

        self.assertIsNone(response.agent_insight)
        insight_repository.get_latest_for_period.assert_called_once_with(
            period_start=self.period_start,
            period_end=self.period_end,
        )


if __name__ == "__main__":
    unittest.main()
