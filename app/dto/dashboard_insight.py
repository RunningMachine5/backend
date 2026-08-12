from datetime import datetime
from typing import Any
from pydantic import BaseModel

class DashboardInsightGenerateRequest(BaseModel):
    # 분석 시작/종료 시간
    period_start: datetime
    period_end: datetime

class DashboardInsightPeriod(BaseModel):
    current_start: datetime
    current_end: datetime
    comparison_start: datetime
    comparison_end: datetime

class DashboardInsightAxis(BaseModel):
    label: str
    unit: str | None = None

class DashboardInsightSeries(BaseModel):
    name: str
    values: list[int]

class DashboardInsightCause(BaseModel):
    label: str
    current_count: int
    previous_count: int
    increase_count: int
    increase_rate: float | None
    suspicious_amount: int
    evidence_case_ids: list[str]

class DashboardInsightSourceRecord(BaseModel):
    case_id: str
    account_number: str
    transaction_amount: int
    transaction_time: datetime
    risk_score: int | None
    risk_grade: str | None
    primary_fraud_type: str | None
    type_scores: dict[str, float]
    matched_components: dict[str, list[str]]
    transaction_features: dict[str, Any]


class DashboardInsightChartSpec(BaseModel):
    chart_type: str
    period: DashboardInsightPeriod
    x_axis: DashboardInsightAxis
    y_axis: DashboardInsightAxis
    series: list[DashboardInsightSeries]
    items: list[DashboardInsightCause]

class DashboardInsightResponse(BaseModel):
    # 분석 결과 DTO
    insight_id : str
    title : str
    summary : str
    chart_spec : DashboardInsightChartSpec
    created_at : datetime
