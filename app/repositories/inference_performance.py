"""최근 ML 추론 성능을 조회한다."""

from dataclasses import dataclass
from datetime import datetime
from math import ceil

from sqlmodel import Session, select

from app.data.model.ml_prediction_result import MLPredictionResult


@dataclass(frozen=True)
class InferencePerformanceSummary:
    inference_count: int
    p95_latency_ms: int | None
    latest_inference_at: datetime | None


@dataclass(frozen=True)
class InferenceThroughputSummary:
    completed_count: int
    normal_count: int
    fraud_count: int
    normal_series: list[dict[str, object]]
    fraud_series: list[dict[str, object]]


class InferencePerformanceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def summarize_since(self, since: datetime) -> InferencePerformanceSummary:
        """주어진 시각 이후의 추론 건수와 P95 응답 시간을 계산한다."""

        predictions = list(
            self.session.exec(
                select(MLPredictionResult).where(
                    MLPredictionResult.created_at >= since
                )
            ).all()
        )
        if not predictions:
            return InferencePerformanceSummary(0, None, None)

        latencies = sorted(item.latency_ms for item in predictions)
        p95_index = ceil(len(latencies) * 0.95) - 1
        return InferencePerformanceSummary(
            inference_count=len(predictions),
            p95_latency_ms=latencies[p95_index],
            latest_inference_at=max(item.created_at for item in predictions),
        )

    def summarize_throughput(
        self,
        since: datetime,
        alignment_seconds: int,
    ) -> InferenceThroughputSummary:
        """DB에 저장된 온라인 분석 결과를 차트 구간별로 묶는다."""

        predictions = list(
            self.session.exec(
                select(
                    MLPredictionResult.created_at,
                    MLPredictionResult.predict_result,
                ).where(
                    MLPredictionResult.created_at >= since
                )
            ).all()
        )
        alignment_minutes = alignment_seconds // 60
        buckets: dict[datetime, dict[str, int]] = {}
        for created_at, predict_result in predictions:
            bucket_at = created_at.replace(
                minute=(created_at.minute // alignment_minutes) * alignment_minutes,
                second=0,
                microsecond=0,
            )
            bucket = buckets.setdefault(
                bucket_at,
                {"normal": 0, "fraud": 0},
            )
            bucket["fraud" if predict_result else "normal"] += 1

        def series(name: str) -> list[dict[str, object]]:
            return [
                {"timestamp": timestamp, "value": counts[name]}
                for timestamp, counts in sorted(buckets.items())
            ]

        fraud_count = sum(1 for _, predict_result in predictions if predict_result)
        return InferenceThroughputSummary(
            completed_count=len(predictions),
            normal_count=len(predictions) - fraud_count,
            fraud_count=fraud_count,
            normal_series=series("normal"),
            fraud_series=series("fraud"),
        )


__all__ = [
    "InferencePerformanceRepository",
    "InferencePerformanceSummary",
    "InferenceThroughputSummary",
]
