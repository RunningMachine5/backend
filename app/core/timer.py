import logging
from contextlib import contextmanager
from time import perf_counter

logger = logging.getLogger("uvicorn.error")


class SectionTimer:
    """구간별 소요 시간(ms)을 기록하고 요약해주는 유틸리티"""

    def __init__(self, name: str = "API") -> None:
        self.name = name
        self.timings: dict[str, float] = {}
        self._start_total = perf_counter()

    @contextmanager
    def measure(self, section_name: str):
        start = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = round((perf_counter() - start) * 1000, 2)
            self.timings[section_name] = elapsed_ms

    @property
    def total_ms(self) -> float:
        return round((perf_counter() - self._start_total) * 1000, 2)

    def log_summary(self) -> None:
        """구간별 소요 시간과 전체 시간을 출력한다."""
        details = ", ".join(f"{k}: {v:.2f}ms" for k, v in self.timings.items())
        msg = f"[TIMER] [{self.name}] Total: {self.total_ms:.2f}ms | {details}"
        print(f"\n{msg}\n", flush=True)
        logger.info(msg)


__all__ = ["SectionTimer"]
