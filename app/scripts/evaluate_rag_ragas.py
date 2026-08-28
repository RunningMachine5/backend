"""챗봇 RAG 경로를 평가하고 지표·토큰·비용 리포트를 저장한다.

실제 LLM과 임베딩 API를 사용하며, 기본 저장 위치는 ``evals/rag/reports``다.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session  # noqa: E402

from app.core.config import COHERE_RERANK_ENABLED  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.services.rag.chatbot_retriever import retriever_source  # noqa: E402
from app.services.rag.cohere_reranker import CohereReranker  # noqa: E402
from app.services.rag.golden_dataset import CATEGORIES, load_golden_cases  # noqa: E402
from app.services.rag.ragas_evaluation import RunResult, evaluate_rag  # noqa: E402


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 값이어야 합니다")
    return number


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS 기반 RAG 평가")
    parser.add_argument(
        "--category",
        choices=CATEGORIES,
        action="append",
        help="이 카테고리만 평가한다 (여러 번 지정 가능)",
    )
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N건만 평가")
    parser.add_argument(
        "--out",
        default=None,
        help="리포트 저장 경로 (생략 시 evals/rag/reports/ 아래에 실행 시각으로 저장)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="파일로 저장하지 않는다 (stdout 으로만 받을 때)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="진행 상황을 출력하지 않는다"
    )
    parser.add_argument(
        "--rerank-rpm",
        type=_positive_float,
        default=None,
        help=(
            "Cohere 리랭커 API 호출을 분당 N회로 제한한다. "
            "평가용 키는 9를 권장한다"
        ),
    )
    return parser.parse_args(argv)


class _RerankRateLimiter:
    """리랭커 요청 시작 사이에 일정 간격을 두는 프로세스 단위 제한기."""

    def __init__(
        self,
        requests_per_minute: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute는 0보다 커야 합니다")
        self.minimum_interval = 60.0 / requests_per_minute
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last_request_at: float | None = None
        self._lock = threading.Lock()

    def __call__(self) -> None:
        # Cohere 재시도도 실제 API 요청이므로 같은 제한기를 반드시 거친다.
        with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                wait_seconds = self.minimum_interval - (
                    now - self._last_request_at
                )
                if wait_seconds > 0:
                    self._sleep(wait_seconds)
                    now = self._clock()
            self._last_request_at = now


def _default_report_path() -> Path:
    """evals/rag/reports/rag-eval-<실행시각>.json"""

    root = Path(__file__).resolve().parents[2]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / "evals" / "rag" / "reports" / f"rag-eval-{stamp}.json"


def _log(message: str) -> None:
    """진행 상황은 stderr 로. stdout 은 리포트 JSON 전용이다."""

    print(message, file=sys.stderr, flush=True)


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}분 {secs:02d}초" if minutes else f"{secs}초"


class _ProgressPrinter:
    """사례별 진행 상황과 남은 시간 추정을 stderr 에 찍는다."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def __call__(self, index: int, total: int, result: RunResult) -> None:
        elapsed = time.monotonic() - self.started
        remaining = (elapsed / index) * (total - index) if index else 0.0

        # 한글은 터미널에서 두 칸을 차지해 문자열 폭 맞추기가 어긋난다.
        # 폭을 맞춰야 하는 자리에는 숫자·영문만 두고 한글은 고정 라벨로만 쓴다.
        flag = "기권" if result.abstained else "  "
        if result.error:
            flag = "오류"

        _log(
            f"  [{index:3d}/{total:3d}] {result.case.id:<14}"
            f" 질의 {len(result.search_queries):2d}"
            f" | 청크 {len(result.retrieved_contexts):2d}"
            f" | {flag}"
            f" | {result.elapsed_seconds:5.1f}s"
            f" | 남은 시간 ~{_format_duration(remaining)}"
            + (f" | {result.error}" if result.error else "")
        )


def _print_summary(report: dict) -> None:
    """평가가 끝난 뒤 핵심 수치만 stderr 에 요약한다."""

    overall = report["overall"]
    _log("\n요약 (자세한 내용은 저장된 리포트 JSON)")
    for key in (
        "context_precision",
        "context_recall",
        "faithfulness",
        "factual_correctness",
        # f1 이 어느 쪽에서 깎였는지 보려고 두 방향을 따로 찍는다.
        "factual_correctness_precision",
        "factual_correctness_recall",
        "abstain_rate",
        "avg_elapsed_seconds",
    ):
        value = overall.get(key)
        _log(f"  {key:<30} {'-' if value is None else f'{value:.4f}'}")

    _log(
        "\n  카테고리별 (context_precision / context_recall / faithfulness"
        " / factual_correctness [precision·recall])"
    )
    for category, row in report["by_category"].items():
        values = " / ".join(
            "-" if row.get(k) is None else f"{row[k]:.3f}"
            for k in (
                "context_precision",
                "context_recall",
                "faithfulness",
                "factual_correctness",
            )
        )
        pair = " · ".join(
            "-" if row.get(k) is None else f"{row[k]:.3f}"
            for k in ("factual_correctness_precision", "factual_correctness_recall")
        )
        _log(f"    {category:<15} {row['count']:>3}건  {values}  [{pair}]")

    _print_usage(report.get("usage"))

    if report["errors"]:
        _log(f"\n  오류 {len(report['errors'])}건: {report['errors']}")


def _print_usage(usage: dict | None) -> None:
    """토큰·비용을 파이프라인(운영에서 실제로 드는 몫)과 심판으로 나눠 찍는다."""

    if not usage:
        return

    _log("\n  토큰·비용 (모델별 내역은 리포트 JSON 의 usage)")
    # 한글은 터미널에서 두 칸을 차지해 폭 지정이 어긋난다. 라벨은 눈으로 맞춘
    # 고정 문자열로 두고, 폭을 맞춰야 하는 자리에는 숫자만 넣는다.
    for phase, label in (("pipeline", "파이프라인 "), ("judge", "RAGAS 심판 ")):
        row = usage[phase]
        suffix = "" if row["total_cost_known"] else " (일부 모델 단가 미상, 과소 추정)"
        per_case = row.get("cost_usd_per_case")
        per_case_text = "" if per_case is None else f" | 사례당 ${per_case:.4f}"
        _log(
            f"    {label} 입력 {row['input_tokens']:>9,} / 출력 {row['output_tokens']:>8,}"
            f" → ${row['total_cost_usd']:.4f}{per_case_text}{suffix}"
        )

    total_suffix = "" if usage["total_cost_known"] else " (과소 추정)"
    _log(f"    합계        ${usage['total_cost_usd']:.4f}{total_suffix}")


def main() -> None:
    args = _parse_args()

    cases = load_golden_cases()
    if args.category:
        wanted = set(args.category)
        cases = tuple(c for c in cases if c.category in wanted)
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        sys.exit("평가할 사례가 없습니다.")

    quiet = args.quiet
    on_case = None if quiet else _ProgressPrinter()
    on_phase = None if quiet else _log
    retriever = retriever_source
    if not COHERE_RERANK_ENABLED:
        # 리랭킹을 끈 상태에서는 retriever_source 가 리랭커를 부르지 않으므로
        # 호출 제한을 걸어봐야 의미가 없다. 착각하지 않도록 알린다.
        if not quiet:
            _log(
                "COHERE_RERANK_ENABLED=false 라 벡터 검색 순위로 평가합니다"
                + (" (--rerank-rpm 은 무시됩니다)" if args.rerank_rpm else "")
            )
    elif args.rerank_rpm is not None:
        limiter = _RerankRateLimiter(args.rerank_rpm)
        retriever = partial(
            retriever_source,
            reranker=CohereReranker(before_request=limiter),
        )
        if not quiet:
            _log(
                "Cohere 리랭커 호출 제한: "
                f"{args.rerank_rpm:g} RPM "
                f"(요청 간 최소 {limiter.minimum_interval:.2f}초)"
            )

    started = time.monotonic()
    with Session(engine) as session:
        report = evaluate_rag(
            cases,
            session,
            retriever=retriever,
            on_case=on_case,
            on_phase=on_phase,
        )

    payload = json.dumps(report, ensure_ascii=False, indent=2)

    saved: Path | None = None
    if not args.no_save:
        saved = Path(args.out) if args.out else _default_report_path()
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(payload, encoding="utf-8")

    if not quiet:
        _print_summary(report)
        _log(f"\n총 소요 {_format_duration(time.monotonic() - started)}")
        if saved is not None:
            _log(f"리포트 저장: {saved}")

    # 터미널에 그대로 실행한 경우에는 JSON 을 화면에 쏟지 않는다. 파이프나
    # 리다이렉트로 받을 때만 stdout 으로 내보내 다른 도구에 물릴 수 있게 한다.
    if not sys.stdout.isatty():
        print(payload)
    elif saved is None:
        _log("\n--no-save 라 리포트를 저장하지 않았습니다. --out 으로 경로를 지정하세요.")


if __name__ == "__main__":
    main()
