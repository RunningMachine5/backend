# repository에서 가져온 원본 rows를 프론트가 바로 그래프로 그릴 수 있는 dto로 제공함

from collections import defaultdict
from datetime import datetime, timedelta

from app.dto.dashboard import (
    DashboardOverviewResponse,
    DashboardOverviewPeriod,
    DashboardAgentInsight,
    DashboardOverviewSummary,
    PriorityTrendPoint,
    DistributionItem,
    SuspiciousTrendPoint
)

from app.repositories.dashboard_overview import (
    DashboardOverviewRepository,
    DashboardSuspiciousRow,
)

class DashboardOverviewService:
    def __init__(
        self,
        repository: DashboardOverviewRepository
    ) -> None:
        self.repository = repository

    # 메인화면 맨 위 카드 값들임
    def get_overview(
        self,
        period_start: datetime,
        period_end: datetime
    ) -> DashboardOverviewResponse:
        self._validate_period(period_start, period_end)

        # 전체 거래 수
        total_transaction_count = self.repository.count_transactions(
            period_start=period_start,
            period_end=period_end
        )

        # 룰 분석 완료 된 수
        rule_analysis_completed_count = (
            self.repository.count_rule_analysis_completed(
                period_start=period_start,
                period_end=period_end,
            )
        )

        # 의심 거래 건수
        suspicious_rows = self.repository.list_suspicious_rows(
            period_start=period_start,
            period_end=period_end,
        )

        # 대시보드 에이전트 최근 분석 값
        latest_insight = self.repository.get_latest_agent_insight()

        return DashboardOverviewResponse(
            period=DashboardOverviewPeriod(
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
            ),
            summary=self._build_summary(
                total_transaction_count=total_transaction_count,
                rule_analysis_completed_count=rule_analysis_completed_count,
                suspicious_rows=suspicious_rows,
            ),
            priority_trend=self._build_priority_trend(
                period_start=period_start,
                period_end=period_end,
                suspicious_rows=suspicious_rows,
            ),
            suspicious_trend=self._build_suspicious_trend(
                period_start=period_start,
                period_end=period_end,
                suspicious_rows=suspicious_rows,
            ),
            risk_grade_distribution=self._build_risk_grade_distribution(
                suspicious_rows
            ),
            channel_distribution=self._build_channel_distribution(
                suspicious_rows
            ),
            agent_insight=(
                DashboardAgentInsight(
                    insight_id=latest_insight.insight_id,
                    title=latest_insight.title,
                    summary=latest_insight.summary or "",
                    chart_spec=latest_insight.chart_spec or {},
                    created_at=latest_insight.created_at.isoformat(),
                )
                if latest_insight is not None
                else None
            ),
        )

    # 유효한 시간인지 체크하는 함수
    @staticmethod
    def _validate_period(
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        if period_start.tzinfo is None:
            raise ValueError("period_start에는 타임존이 필요하다.")

        if period_end.tzinfo is None:
            raise ValueError("period_end에는 타임존이 필요하다.")

        if period_start >= period_end:
            raise ValueError("period_start는 period_end보다 빨라야 한다.")

    # 요약 카드 계산 함수
    @staticmethod
    def _build_summary(
        *,
        total_transaction_count: int,
        rule_analysis_completed_count: int,
        suspicious_rows: list[DashboardSuspiciousRow],
    ) -> DashboardOverviewSummary:
        return DashboardOverviewSummary(
            total_transaction_count=total_transaction_count,
            suspicious_transaction_count=len(suspicious_rows),
            priority_review_count=sum(
                row.risk_grade in {"VERY_HIGH", "HIGH"}
                for row in suspicious_rows
            ),
            suspicious_amount=sum(
                abs(row.transaction_amount)
                for row in suspicious_rows
            ),
            rule_analysis_completed_count=rule_analysis_completed_count,
        )

    # 우선순위
    def _build_priority_trend(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        suspicious_rows: list[DashboardSuspiciousRow]
    ) -> list[PriorityTrendPoint]:
        buckets = self._empty_date_buckets(
            period_start,
            period_end
        )

        for row in suspicious_rows:
            date = self._date_label(row.transaction_datetime)

            if date not in buckets:
                continue

            if row.risk_grade == "VERY_HIGH":
                buckets[date]["very_high_count"] += 1

            if row.risk_grade == "HIGH":
                buckets[date]["high_count"] += 1

        return [
            PriorityTrendPoint(
                date=date,
                very_high_count=value["very_high_count"],
                high_count=value["high_count"],
                total_count=(
                    value["very_high_count"]
                    + value["high_count"]
                ),
            )
            for date, value in buckets.items()
        ]

    # 사기 트렌드
    def _build_suspicious_trend(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        suspicious_rows: list[DashboardSuspiciousRow]
    ) -> list[SuspiciousTrendPoint]:
        buckets = self._empty_date_buckets(
            period_start,
            period_end
        )

        for row in suspicious_rows:
            date = self._date_label(row.transaction_datetime)

            if date not in buckets:
                continue

            buckets[date]["suspicious_count"] += 1
            buckets[date]["suspicious_amount"] += abs(
                row.transaction_amount
            )

        return[
            SuspiciousTrendPoint(
                date=date,
                suspicious_count=value["suspicious_count"],
                suspicious_amount=value["suspicious_amount"]
            )
            for date, value in buckets.items()
        ]

    # 위험등급별/채널별 통계
    @staticmethod
    def _build_distribution(
        *,
        suspicious_rows: list[DashboardSuspiciousRow],
        group_by: str,
        order: list[str]
    ) -> list[DistributionItem]:
        counts = defaultdict(int)
        amounts = defaultdict(int)

        for row in suspicious_rows:
            if group_by == "risk_grade":
                label = row.risk_grade or "UNKNOWN"
            elif group_by == "channel":
                label = row.channel or "UNKNOWN"
            else:
                raise ValueError("지원하지 않는 group_by 입니다.")

            counts[label] += 1
            amounts[label] += abs(row.transaction_amount)

        labels = [
            *order,
            *sorted(label for label in counts if label not in order)
        ]

        return [
            DistributionItem(
                label=label,
                count=counts[label],
                amount=amounts[label]
            )
            for label in labels
            if counts[label] > 0 or label in order
        ]

    def _build_risk_grade_distribution(
        self,
        suspicious_rows: list[DashboardSuspiciousRow],
    ) -> list[DistributionItem]:
        return self._build_distribution(
            suspicious_rows=suspicious_rows,
            group_by="risk_grade",
            order=["VERY_HIGH", "HIGH", "MEDIUM", "LOW"],
        )

    def _build_channel_distribution(
        self,
        suspicious_rows: list[DashboardSuspiciousRow],
    ) -> list[DistributionItem]:
        return self._build_distribution(
            suspicious_rows=suspicious_rows,
            group_by="channel",
            order=["mobile", "internet", "ATM", "Others"],
        )

    # 날짜 헬퍼 2개
    @staticmethod
    def _empty_date_buckets(
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, dict[str, int]]:
        buckets: dict[str, dict[str, int]] = {}

        current_date = period_start.date()
        last_date = (period_end - timedelta(microseconds=1)).date()

        while current_date <= last_date:
            label =f"{current_date.month}/{current_date.day}"
            buckets[label] = {
                "very_high_count": 0,
                "high_count": 0,
                "suspicious_count": 0,
                "suspicious_amount": 0
            }
            current_date += timedelta(days=1)

        return buckets

    @staticmethod
    def _date_label(value: datetime) -> str:
        return f"{value.month}/{value.day}"

    
