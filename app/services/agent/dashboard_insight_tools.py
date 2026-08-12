# 대시보드 에이전트가 레포지토리를 직접 다루지 않도록 조회 기능을 제공함
# 여기에 작성하는 함수가 Tool임

from datetime import datetime

from app.repositories.dashboard_insight import (
    DashboardInsightRepository,
)
from app.services.agent.dashboard_insight_analyzer import(
    DashboardInsightAnalyzer,
)

class DashboardInsightTools:
    def __init__(
            self,
            repository: DashboardInsightRepository,
            analyzer: DashboardInsightAnalyzer,
    ) -> None:
        self.repository = repository
        self.analyzer = analyzer
            
    # 기간 내 이상거래 건수와 총 금액
    def get_period_summary(
        self,
        period_start: datetime,
        period_end: datetime,
    ):
        records = self.repository.get_period_records(
            period_start=period_start,
            period_end=period_end,
        )
        return self.analyzer.summarize(records)
    
    # 현재 기간과 이전 기간 증감 비교
    def compare_previous_period(
            self,
            period_start: datetime,
            period_end: datetime,
    ):
        duration = period_end - period_start

        current_records = self.repository.get_period_records(
            period_start,
            period_end,
        )
        previous_records = self.repository.get_period_records(
            period_start - duration,
            period_start,
        )

        return self.analyzer.compare(
            current_records=current_records,
            previous_records=previous_records,
        )
    
    # 거래 특성, 사기 유형, 룰 근거별 집계
    def aggregate_by_evidence(
            self,
            period_start: datetime,
            period_end: datetime,
        ):
        records = self.repository.get_period_records(
            period_start,
            period_end,
        )
        return self.analyzer.aggregate(records)

    # 특정 원인의 대표 사건 id 조회
    def get_representative_cases(
            self,
            *,
            cause_codes: tuple[str, ...],
            period_start: datetime,
            period_end: datetime,
            limit: int = 3,
    ):
        records = self.repository.get_period_records(
            period_start,
            period_end,
        )

        return self.analyzer.get_representative_cases(
            records=records,
            cause_codes=cause_codes,
            limit=limit,
        )
