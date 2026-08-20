from sqlmodel import Session

from app.data.model.agent import AgentDashboardInsight
from app.dto.dashboard_insight import (
    DashboardInsightChartSpec,
    DashboardInsightGenerateRequest,
    DashboardInsightResponse,
)
from app.repositories.dashboard_insight import (
    DashboardInsightRepository,
)
from app.services.agent.dashboard_insight_agent import (
    DashboardInsightAgent,
)
from app.services.agent.dashboard_insight_analyzer import (
    DashboardInsightAnalyzer,
)
from app.services.agent.dashboard_insight_tools import (
    DashboardInsightTools,
)
from app.services.agent.dashboard_insight_llm import (
    DashboardInsightLLM,
)

class DashboardInsightPipeline:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DashboardInsightRepository(session)

    def _build_agent(self) -> DashboardInsightAgent:
        analyzer = DashboardInsightAnalyzer()
        tools = DashboardInsightTools(
            repository=self.repository,
            analyzer=analyzer,
        )
        llm = DashboardInsightLLM()
        return DashboardInsightAgent(
            tools=tools,
            llm=llm,
        )

    def run(
        self,
        request: DashboardInsightGenerateRequest
    ) -> DashboardInsightResponse:
        self._validate_period(request)

        # force_refresh가 아닐 때만 같은 기간의 결과를 재사용한다.
        if not request.force_refresh:
            existing_insight = self.repository.get_latest_for_period(
                period_start=request.period_start,
                period_end=request.period_end,
            )
            if existing_insight is not None:
                return self._to_response(existing_insight)

        # Agent가 기간 비교, 원인 선정, 차트 생성을 수행한다.
        draft = self._build_agent().run(
            period_start=request.period_start,
            period_end=request.period_end,
        )

        try:
            insight = self.repository.add(
                title=draft.title,
                summary=draft.summary,
                chart_spec=draft.chart_spec,
            )

            self.session.commit()
            self.session.refresh(insight)

        except Exception:
            self.session.rollback()
            raise

        return DashboardInsightResponse(
            insight_id=insight.insight_id,
            title=insight.title,
            summary=insight.summary or "",
            chart_spec=draft.chart_spec,
            created_at=insight.created_at,
        )

    @staticmethod
    def _to_response(
        insight: AgentDashboardInsight,
    ) -> DashboardInsightResponse:
        return DashboardInsightResponse(
            insight_id=insight.insight_id,
            title=insight.title,
            summary=insight.summary or "",
            chart_spec=DashboardInsightChartSpec.model_validate(
                insight.chart_spec
            ),
            created_at=insight.created_at,
        )

    @staticmethod
    def _validate_period(
        request: DashboardInsightGenerateRequest,
    ) -> None:
        if request.period_start.tzinfo is None:
            raise ValueError(
                "period_start에는 타임존이 필요하다."
            )

        if request.period_end.tzinfo is None:
            raise ValueError(
                "period_end에는 타임존이 필요하다."
            )

        if request.period_start >= request.period_end:
            raise ValueError(
                "period_start는 period_end보다 빨라야 한다."
            )
