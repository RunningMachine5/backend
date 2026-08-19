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


__all__ = ["InferencePerformanceRepository", "InferencePerformanceSummary"]
