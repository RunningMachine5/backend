from sqlmodel import Session

from app.dto.dashboard_insight import (
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
        self.analyzer = DashboardInsightAnalyzer()
        self.tools = DashboardInsightTools(
            repository=self.repository,
            analyzer=self.analyzer,
        )
        self.llm = DashboardInsightLLM()
        self.agent = DashboardInsightAgent(
            tools=self.tools,
            llm=self.llm
        )

    def run(
        self,
        request: DashboardInsightGenerateRequest
    ) -> DashboardInsightResponse:
        self._validate_period(request)

        # Agent가 기간 비교, 원인 선정, 차트 생성을 수행한다.
        draft = self.agent.run(
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