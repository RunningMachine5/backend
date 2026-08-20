"""골든셋으로 실제 RAG 파이프라인(질의 분해·검색·응답 생성)만 돌려 토큰 사용량과
비용을 리포트한다. RAGAS 심판 LLM은 호출하지 않는다.

evaluate_rag_ragas 의 가장 비싼 부분은 [2/3] RAGAS 채점(사례당 지표 6개, 심판 LLM
반복 호출)이다. 채점 없이 파이프라인 비용만 먼저 가늠하고 싶을 때 이 스크립트를 쓴다.
먼저 app.services.rag.docs_embedding 으로 코퍼스를 적재해야 한다.

리포트는 evaluate_rag_ragas 와 동일하게 **항상 파일로 저장한다**. 저장 경로는 --out 으로
지정하고, 지정하지 않으면 evals/rag/reports/ 아래에 실행 시각으로 파일을 만든다.

진행 상황은 stderr 로 나간다. stdout 은 파이프나 리다이렉트로 넘길 때만 JSON 을
내보내고(다른 도구에 물릴 수 있게), 터미널에서 그냥 실행하면 화면을 채우지 않는다.

    uv run --env-file .env --group eval python -m app.scripts.evaluate_rag_cost
    uv run --env-file .env --group eval python -m app.scripts.evaluate_rag_cost --limit 5
    uv run --env-file .env --group eval python -m app.scripts.evaluate_rag_cost \
        --out evals/rag/reports/rag-cost-before-chunking-fix.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langchain_core.callbacks import get_usage_metadata_callback  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.services.rag.golden_dataset import CATEGORIES, load_golden_cases  # noqa: E402
from app.services.rag.ragas_evaluation import RunResult, run_cases  # noqa: E402
from app.services.rag.token_pricing import build_usage_report  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAG 파이프라인 토큰 사용량·비용만 리포트 (RAGAS 채점 없음)"
    )
    parser.add_argument(
        "--category",
        choices=CATEGORIES,
        action="append",
        help="이 카테고리만 실행한다 (여러 번 지정 가능)",
    )
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N건만 실행")
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
    return parser.parse_args()


def _default_report_path() -> Path:
    """evals/rag/reports/rag-cost-<실행시각>.json"""

    root = Path(__file__).resolve().parents[2]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / "evals" / "rag" / "reports" / f"rag-cost-{stamp}.json"


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


def _build_report(results: list[RunResult], usage_metadata: dict) -> dict:
    """RAGAS 채점 없이 실행 결과와 토큰/비용만 담은 리포트."""

    return {
        "case_count": len(results),
        # evaluate_rag_ragas 리포트의 usage.pipeline 과 같은 형식이라 두 리포트를 그대로 비교할 수 있다.
        "usage": build_usage_report(usage_metadata, case_count=len(results)),
        "avg_elapsed_seconds": round(
            sum(r.elapsed_seconds for r in results) / len(results), 4
        )
        if results
        else None,
        "abstain_rate": round(
            sum(1 for r in results if r.abstained) / len(results), 4
        )
        if results
        else None,
        "errors": [{"id": r.case.id, "error": r.error} for r in results if r.error],
        "cases": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "search_queries": list(r.search_queries),
                "retrieved_locators": [f"{t} p{p}" for t, p in r.retrieved_locators],
                "abstained": r.abstained,
                "elapsed_seconds": round(r.elapsed_seconds, 2),
                "response": r.response,
            }
            for r in results
        ],
    }


def _print_summary(report: dict) -> None:
    _log("\n요약 (자세한 내용은 저장된 리포트 JSON)")
    _log(f"  사례 수                    {report['case_count']}")
    avg = report["avg_elapsed_seconds"]
    _log(f"  avg_elapsed_seconds         {'-' if avg is None else f'{avg:.4f}'}")
    rate = report["abstain_rate"]
    _log(f"  abstain_rate                {'-' if rate is None else f'{rate:.4f}'}")

    _log("\n  모델별 토큰·비용")
    for row in report["usage"]["by_model"]:
        cost_text = "단가 미상" if row["cost_usd"] is None else f"${row['cost_usd']:.4f}"
        cached = (
            f", 캐시입력 {row['cached_input_tokens']:,}"
            if row["cached_input_tokens"]
            else ""
        )
        _log(
            f"    {row['model']:<28} 입력 {row['input_tokens']:>8,} / "
            f"출력 {row['output_tokens']:>8,}{cached} → {cost_text}"
        )

    usage = report["usage"]
    suffix = "" if usage["total_cost_known"] else " (일부 모델 단가 미상, 과소 추정)"
    _log(f"  합계 비용                   ${usage['total_cost_usd']:.4f}{suffix}")
    per_case = usage.get("cost_usd_per_case")
    if per_case is not None:
        _log(f"  사례당 비용                 ${per_case:.4f}")

    if report["errors"]:
        _log(f"\n  오류 {len(report['errors'])}건: {report['errors']}")


def main() -> None:
    args = _parse_args()

    cases = load_golden_cases()
    if args.category:
        wanted = set(args.category)
        cases = tuple(c for c in cases if c.category in wanted)
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        sys.exit("실행할 사례가 없습니다.")

    quiet = args.quiet
    on_case = None if quiet else _ProgressPrinter()

    started = time.monotonic()
    with Session(engine) as session:
        with get_usage_metadata_callback() as usage_callback:
            results = run_cases(cases, session, on_case=on_case)
        usage_metadata = usage_callback.usage_metadata

    report = _build_report(results, usage_metadata)
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
