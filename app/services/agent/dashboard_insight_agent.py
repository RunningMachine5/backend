# 결과 3~5개 선택 및 그래프 생성
# 결과를 보고 뭐를 보여줄지 결정함

from dataclasses import dataclass
from datetime import datetime

from app.dto.dashboard_insight import (
    DashboardInsightAxis,
    DashboardInsightCause,
    DashboardInsightChartSpec,
    DashboardInsightPeriod,
    DashboardInsightSeries,
)
from app.services.agent.dashboard_insight_tools import (
    DashboardInsightTools,
)
from app.services.agent.dashboard_insight_llm import (
    DashboardInsightLLM,
)

@dataclass(frozen=True)
class DashboardInsightDraft:
    title: str
    summary: str
    chart_spec: DashboardInsightChartSpec

class DashboardInsightAgent:
    def __init__(
        self,
        tools: DashboardInsightTools,
        llm: DashboardInsightLLM | None = None
    ) -> None:
        self.tools = tools
        self.llm = llm

    def run(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> DashboardInsightDraft:
        duration = period_end - period_start
        previous_start = period_start - duration
        previous_end = period_start

        comparison = self.tools.compare_previous_period(
            period_start,
            period_end,
        )

        current_causes = self.tools.aggregate_by_evidence(
            period_start,
            period_end
        )
        previous_causes = self.tools.aggregate_by_evidence(
            previous_start,
            previous_end
        )

        previous_by_code = {
            cause.cause_codes: cause
            for cause in previous_causes
        }

        candidates = []

        for current in current_causes:
            previous = previous_by_code.get(
                current.cause_codes
            )
            previous_count = (
                previous.transaction_count
                if previous
                else 0
            )

            increase_count = (
                current.transaction_count - previous_count
            )

            increase_rate = (
                None
                if previous_count == 0
                else increase_count / previous_count
            )

            # 증가한 원인만 우선 후보로 사용함
            if increase_count > 0 :
                candidates.append(
                    {
                    "cause": current,
                    "previous_count": previous_count,
                    "increase_count": increase_count,
                    "increase_rate": increase_rate,
                    }
                )

        # 증가 원인이 없으면 현재 기간 상위 원인을 사용함
        if not candidates:
            candidates = [
                {
                    "cause": cause,
                    "previous_count": (
                        previous_by_code[
                            cause.cause_codes
                        ].transaction_count
                        if cause.cause_codes
                        in previous_by_code
                        else 0
                    ),
                    "increase_count": 0,
                    "increase_rate": 0.0
                }
                for cause in current_causes
            ]

        candidates.sort(
            key=lambda item: (
                -item["increase_count"],
                -(
                    item["increase_rate"]
                    if item["increase_rate"] is not None
                    else float("inf")
                ),
                -item["cause"].transaction_count,
                -item["cause"].suspicious_amount,
            )
        )

        llm_candidates = [
            {
                "label": item["cause"].label,
                "current_count": item["cause"].transaction_count,
                "previous_count": item["previous_count"],
                "increase_count": item["increase_count"],
                "increase_rate": item["increase_rate"],
                "suspicious_amount": item["cause"].suspicious_amount,
            }
            for item in candidates
        ]

        title=""
        summary=""
        selected_labels: list[str] = []

        if self.llm is not None:
            try:
                llm_result = self.llm.select_and_describe(
                    current_count=comparison.current.transaction_count,
                    previous_count=comparison.previous.transaction_count,
                    candidates=llm_candidates,
                )
                selected_labels = llm_result.selected_labels
                title = llm_result.title
                summary = llm_result.summary
            except Exception:
                selected_labels = []

        if selected_labels:
            selected = [
                item
                for item in candidates
                if item["cause"].label in selected_labels
            ][:5]
            if not selected:
                title = ""
                summary = ""
                selected = candidates[:5]
        else:
            selected = candidates[:5]

        items: list[DashboardInsightCause] = []
        for candidate in selected:
            cause = candidate["cause"]

            evidence_case_ids = (
                self.tools.get_representative_cases(
                    cause_codes = cause.cause_codes,
                    period_start = period_start,
                    period_end = period_end,
                    limit = 3
                )
            )

            items.append(
                DashboardInsightCause(
                    label=cause.label,
                    current_count=(
                        cause.transaction_count
                    ),
                    previous_count=(
                        candidate["previous_count"]
                    ),
                    increase_count=(
                        candidate["increase_count"]
                    ),
                    increase_rate=(
                        candidate["increase_rate"]
                    ),
                    suspicious_amount=(
                        cause.suspicious_amount
                    ),
                    evidence_case_ids=(
                        evidence_case_ids
                    ),
                )
            )

        if not title or not summary:
            title, summary = self._create_description(
                items=items,
                current_count=comparison.current.transaction_count,
                previous_count=comparison.previous.transaction_count,
            )

        chart_spec = DashboardInsightChartSpec(
            chart_type="GROUPED_HORIZONTAL_BAR",
            period=DashboardInsightPeriod(
                current_start=period_start,
                current_end=period_end,
                comparison_start=previous_start,
                comparison_end=previous_end,
            ),
            x_axis=DashboardInsightAxis(
                label="관련 사건 수",
                unit="건",
            ),
            y_axis=DashboardInsightAxis(
                label="이상징후 원인 조합",
            ),
            series=[
                DashboardInsightSeries(
                    name="현재 기간",
                    values=[
                        item.current_count
                        for item in items
                    ],
                ),
                DashboardInsightSeries(
                    name="이전 기간",
                    values=[
                        item.previous_count
                        for item in items
                    ],
                ),
            ],
            items=items,
        )

        return DashboardInsightDraft(
            title=title,
            summary=summary,
            chart_spec=chart_spec,
        )

    @staticmethod
    def _create_description(
        *,
        items: list[DashboardInsightCause],
        current_count: int,
        previous_count: int,
    ) -> tuple[str, str]:
        if not items:
            return (
                "주목할 이상징후 없음",
                "현재 기간에 분석할 이상거래가 없습니다.",
            )

        top = items[0]

        title = f"{top.label} 조합 증가"

        if top.previous_count == 0:
            change_text = (
                f"이전 기간 0건에서 "
                f"현재 {top.current_count}건으로 신규 발생"
            )
        else:
            percentage = round(
                (top.increase_rate or 0) * 100
            )
            change_text = (
                f"이전 기간 {top.previous_count}건 대비 "
                f"현재 {top.current_count}건으로 "
                f"{percentage:+d}% 변화"
            )

        summary = (
            f"전체 이상거래는 이전 기간 "
            f"{previous_count}건에서 현재 "
            f"{current_count}건으로 변경되었습니다. "
            f"{top.label} 원인이 가장 주목되며, "
            f"{change_text}했습니다."
        )

        return title, summary
