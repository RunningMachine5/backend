# 집계/비교/원인 조합 계산

from dataclasses import dataclass
from itertools import combinations

from app.dto.dashboard_insight import (
    DashboardInsightSourceRecord,
)

# 기간 내 이상거래 건수와 총 금액
@dataclass(frozen=True)
class PeriodSummary:
    transaction_count: int
    suspicious_amount: int

# 현재 기간과 이전 기간 증감 비교
@dataclass(frozen=True)
class PeriodComparison:
    current: PeriodSummary
    previous: PeriodSummary
    count_change: int
    amount_change: int

# 거래 특성, 사기 유형, 룰 근거별 집계
@dataclass(frozen=True)
class CauseAggregate:
    cause_codes: tuple[str, ...]
    label: str
    transaction_count: int
    suspicious_amount: int
    case_ids: tuple[str, ...]

class DashboardInsightAnalyzer:
    def summarize(
        self,
        records: list[DashboardInsightSourceRecord]
    ) -> PeriodSummary:
        return PeriodSummary(
            transaction_count = len(records),
            suspicious_amount=sum(
                abs(record.transaction_amount)
                for record in records
            )
        )

    def compare(
        self,
        *,
        current_records: list[DashboardInsightSourceRecord],
        previous_records: list[DashboardInsightSourceRecord]
    ) -> PeriodComparison:
        current = self.summarize(current_records)
        previous = self.summarize(previous_records)

        return PeriodComparison(
            current=current,
            previous=previous,
            count_change=(
                current.transaction_count - previous.transaction_count
            ),
            amount_change=(
                current.suspicious_amount - previous.suspicious_amount
            )
        )

    def aggregate(
        self,
        records: list[DashboardInsightSourceRecord]
    ) -> list[CauseAggregate]:
        aggregates: dict[tuple[str, ...], dict] = {}

        for record in records:
            signals = sorted(self._get_signals(record))
            cause_groups = list(combinations(signals, 2))

            if not cause_groups:
                cause_groups = [
                    (signal,) for signal in signals
                ]

            for cause_codes in cause_groups:
                aggregate = aggregates.setdefault(
                    cause_codes,
                    {
                        "transaction_count": 0,
                        "suspicious_amount": 0,
                        "case_ids": []
                    }
                )

                aggregate["transaction_count"] += 1
                aggregate["suspicious_amount"] += abs(
                    record.transaction_amount
                )
                aggregate["case_ids"].append(record.case_id)

        results = [
            CauseAggregate(
                cause_codes=cause_codes,
                label="+".join(
                    self._display_name(code)
                    for code in cause_codes
                ),
                transaction_count=value[
                    "transaction_count"
                ],
                suspicious_amount=value[
                    "suspicious_amount"
                ],
                case_ids=tuple(
                    dict.fromkeys(value["case_ids"])
                ),
            )
            for cause_codes, value in aggregates.items()
        ]

        results.sort(
            key=lambda result: (
                -result.transaction_count,
                -result.suspicious_amount,
                result.cause_codes
            )
        )

        return results

    def get_representative_cases(
        self,
        *,
        records: list[DashboardInsightSourceRecord],
        cause_codes: tuple[str, ...],
        limit: int,
    )-> list[str]:
        matching_records = [
            record
            for record in records
            if set(cause_codes).issubset(
                self._get_signals(record)
            )
        ]

        matching_records.sort(
            key=lambda record: (
                - (record.risk_score or 0),
                - abs(record.transaction_amount),
                record.case_id
            )
        )

        return list(
            dict.fromkeys(
                record.case_id
                for record in matching_records
            )
        )[:limit]

    def _get_signals(
            self,
            record: DashboardInsightSourceRecord,
    ) -> set[str]:
        signals: set[str] = set()

        # 거래 특성
        for name, value in record.transaction_features.items():
            if isinstance(value, bool) and value:
                signals.add(f"FEATURE:{name.upper()}")

            if name in {"channel", "location"} and value:
                signals.add(
                    f"{name.upper()}:{self._normalize(value)}"
                )

        if abs(record.transaction_amount) >= 10_000_000:
            signals.add("FEATURE:HIGH_AMOUNT")

        # 대표 사기 유형
        fraud_type = record.primary_fraud_type

        if fraud_type is None and record.type_scores:
            fraud_type = max(
                record.type_scores,
                key = record.type_scores.get
            )

        if fraud_type:
            signals.add(
                f"FRAUD_TYPE:{self._normalize(fraud_type)}"
            )

        # Rule 근거
        for(
            fraud_type,
            component_codes
        ) in record.matched_components.items():
            for component_code in component_codes:
                signals.add(
                    "RULE:"
                    f"{self._normalize(fraud_type)}:"
                    f"{self._normalize(component_code)}"
                )
        return signals    


    @staticmethod # 정규화 코드
    def _normalize(value: object) -> str:
        return (
            str(value)
            .strip()
            .upper()
            .replace(" ", "_")
        )

    @staticmethod # 이름 보이기?
    def _display_name(code: str) -> str:
        parts = code.split(":")

        if parts[0] == "CHANNEL":
            return f"{parts[-1]} 채널"
        if parts[0] == "LOCATION":
            return f"{parts[-1]} 지역"

        return parts[-1]
