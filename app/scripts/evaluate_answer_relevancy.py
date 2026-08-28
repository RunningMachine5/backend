"""기존 RAG 평가 리포트에 Answer Relevancy 점수를 추가한다.

파이프라인과 기존 RAGAS 지표를 다시 실행하지 않고, 리포트에 저장된 ``response``와
골든셋의 ``user_input``만 사용한다. 원본은 기본적으로 보존하며 결과는 같은 폴더의
``<원본명>-with-answer-relevancy.json``에 저장한다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from langchain_core.callbacks import get_usage_metadata_callback  # noqa: E402
from langchain_openai import OpenAIEmbeddings  # noqa: E402

from app.services.rag.golden_dataset import (  # noqa: E402
    ANSWERABLE_CATEGORIES,
    GoldenCase,
    load_golden_cases,
)
from app.services.rag.ragas_judge import build_judge_llm  # noqa: E402
from app.services.rag.token_pricing import build_usage_report  # noqa: E402


OUTPUT_SUFFIX = "-with-answer-relevancy"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# 기본 RAGAS 예시는 영어다. 한국어 응답에서 생성한 역질문도 한국어로 유지해 원 질문과
# 같은 언어끼리 임베딩 유사도를 비교하도록 한다.
_SAME_LANGUAGE_INSTRUCTION = (
    "\nGenerate the question in the same language as the answer. Never translate "
    "the answer or the generated question into another language."
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기존 RAG 평가 JSON에 Answer Relevancy를 후채점한다"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="평가 JSON 파일 또는 JSON 파일을 재귀 탐색할 폴더",
    )
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=None,
        help="질문을 읽을 골든셋 JSONL (생략 시 프로젝트 기본 골든셋)",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"질문 유사도 임베딩 모델 (기본: {DEFAULT_EMBEDDING_MODEL})",
    )
    parser.add_argument(
        "--strictness",
        type=_positive_int,
        default=3,
        help="응답마다 생성할 역질문 수 (기본: 3)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="별도 파일을 만들지 않고 입력 JSON을 원자적으로 교체",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="이미 Answer Relevancy가 있거나 결과 파일이 있어도 다시 측정",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="파일별 진행 상황을 출력하지 않음"
    )
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 정수여야 합니다")
    return number


def discover_reports(paths: Iterable[Path]) -> list[Path]:
    """명시한 파일과 폴더 아래 JSON을 중복 없이 정렬해 반환한다."""

    found: dict[Path, None] = {}
    for path in paths:
        if path.is_file():
            if path.suffix.lower() != ".json":
                raise ValueError(f"JSON 파일이 아닙니다: {path}")
            found[path.resolve()] = None
            continue
        if path.is_dir():
            for candidate in path.rglob("*.json"):
                # 기본 출력물을 다음 실행의 새 입력으로 다시 찾지 않는다. 사용자가
                # 파일 경로를 직접 지정한 경우에는 위 분기에서 명시적 입력으로 받는다.
                if not candidate.stem.endswith(OUTPUT_SUFFIX):
                    found[candidate.resolve()] = None
            continue
        raise FileNotFoundError(f"경로가 없습니다: {path}")
    return sorted(found)


def _load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 파싱 실패: {path}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"JSON 최상위 값이 객체가 아닙니다: {path}")
    return report


def is_rag_answer_report(report: dict[str, Any]) -> bool:
    """사례별 생성 답변을 가진 RAG 평가 리포트인지 확인한다."""

    cases = report.get("cases")
    return (
        isinstance(cases, list)
        and bool(cases)
        and all(isinstance(case, dict) and "response" in case for case in cases)
    )


def has_answer_relevancy(report: dict[str, Any]) -> bool:
    overall = report.get("overall")
    return isinstance(overall, dict) and "answer_relevancy" in overall


def build_samples(
    report: dict[str, Any],
    golden_by_id: dict[str, GoldenCase],
) -> tuple[list[dict[str, str]], list[str]]:
    """RAGAS 입력과 빈 응답이라 즉시 0점 처리할 사례 ID를 만든다."""

    samples: list[dict[str, str]] = []
    empty_response_ids: list[str] = []
    seen_ids: set[str] = set()
    for row in report["cases"]:
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("리포트 사례에 유효한 id가 없습니다")
        if case_id in seen_ids:
            raise ValueError(f"리포트에 중복된 사례 id가 있습니다: {case_id}")
        seen_ids.add(case_id)

        golden = golden_by_id.get(case_id)
        if golden is None:
            raise ValueError(f"골든셋에 없는 사례 id입니다: {case_id}")
        if row.get("category") != golden.category:
            raise ValueError(
                f"카테고리가 골든셋과 다릅니다: {case_id} "
                f"({row.get('category')} != {golden.category})"
            )
        if golden.category not in ANSWERABLE_CATEGORIES:
            continue

        response = row.get("response")
        if not isinstance(response, str):
            raise ValueError(f"response가 문자열이 아닙니다: {case_id}")
        if not response.strip():
            empty_response_ids.append(case_id)
            continue
        samples.append(
            {
                "case_id": case_id,
                "user_input": golden.user_input,
                "response": response,
            }
        )
    return samples, empty_response_ids


def score_samples(
    samples: list[dict[str, str]],
    *,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    strictness: int = 3,
) -> tuple[dict[str, float], dict[str, Any]]:
    """RAGAS Answer Relevancy를 계산하고 심판 LLM 사용량을 반환한다."""

    if not samples:
        return {}, build_usage_report({}, case_count=0)

    # 현재 평가 하네스와 호환되는 RAGAS legacy metric을 사용한다. collections의
    # 동명 클래스는 modern LLM/embedding 계약이라 build_judge_llm과 함께 쓸 수 없다.
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics._answer_relevance import (
        ResponseRelevancePrompt,
        ResponseRelevancy,
    )

    prompt = ResponseRelevancePrompt()
    prompt.instruction += _SAME_LANGUAGE_INSTRUCTION
    metric = ResponseRelevancy(
        strictness=strictness,
        question_generation=prompt,
    )
    ragas_samples = [
        {"user_input": row["user_input"], "response": row["response"]}
        for row in samples
    ]
    with get_usage_metadata_callback() as judge_usage:
        result = evaluate(
            dataset=EvaluationDataset.from_list(ragas_samples),
            metrics=[metric],
            llm=build_judge_llm(),
            embeddings=OpenAIEmbeddings(model=embedding_model),
        )

    rows = result.to_pandas().to_dict(orient="records")
    if len(rows) != len(samples):
        raise RuntimeError(
            f"RAGAS 결과 수가 입력과 다릅니다: {len(rows)} != {len(samples)}"
        )

    scores = {}
    for sample, row in zip(samples, rows):
        score = _as_float(row.get("answer_relevancy"))
        if score is None:
            raise RuntimeError(
                f"Answer Relevancy 점수가 없습니다: {sample['case_id']}"
            )
        scores[sample["case_id"]] = score
    usage = build_usage_report(
        dict(judge_usage.usage_metadata), case_count=len(samples)
    )
    return scores, usage


def add_answer_relevancy(
    report: dict[str, Any],
    scores: dict[str, float],
    golden_by_id: dict[str, GoldenCase],
    *,
    judge_usage: dict[str, Any],
    embedding_model: str,
    strictness: int,
    golden_set_path: Path,
) -> dict[str, Any]:
    """사례 점수와 전체·슬라이스 평균을 기존 리포트 사본에 병합한다."""

    updated = copy.deepcopy(report)
    empty_response_count = 0
    for row in updated["cases"]:
        case_id = row["id"]
        row_scores = row.setdefault("scores", {})
        if case_id in scores:
            row_scores["answer_relevancy"] = scores[case_id]
            if not row.get("response", "").strip():
                empty_response_count += 1
        else:
            row_scores.pop("answer_relevancy", None)

    updated.setdefault("overall", {})["answer_relevancy"] = _score_mean(
        updated["cases"], scores
    )
    _merge_group_averages(
        updated,
        "by_category",
        scores,
        golden_by_id,
        lambda case: case.category,
    )
    _merge_group_averages(
        updated,
        "by_difficulty",
        scores,
        golden_by_id,
        lambda case: case.difficulty,
    )
    updated["answer_relevancy_evaluation"] = {
        "metric": "ragas_answer_relevancy",
        "scored_count": len(scores),
        "llm_evaluated_count": len(scores) - empty_response_count,
        "empty_response_count": empty_response_count,
        "excluded_categories": ["unanswerable"],
        "empty_response_score": 0.0,
        "strictness": strictness,
        "judge_model": _judge_model_name(),
        "embedding_model": embedding_model,
        "golden_set": str(golden_set_path.resolve()),
        "golden_set_sha256": hashlib.sha256(golden_set_path.read_bytes()).hexdigest(),
        # 기존 usage.judge는 원래 실행의 6개 지표 비용이므로 바꾸지 않는다.
        # 임베딩 API 사용량은 LangChain 콜백에 잡히지 않아 모델명만 별도 기록한다.
        "judge_usage": judge_usage,
    }
    return updated


def _merge_group_averages(
    report: dict[str, Any],
    section_name: str,
    scores: dict[str, float],
    golden_by_id: dict[str, GoldenCase],
    group_key: Callable[[GoldenCase], str],
) -> None:
    section = report.get(section_name)
    if not isinstance(section, dict):
        return

    grouped: dict[str, list[float]] = defaultdict(list)
    for case_id, score in scores.items():
        grouped[group_key(golden_by_id[case_id])].append(score)
    for group, summary in section.items():
        if isinstance(summary, dict):
            summary["answer_relevancy"] = _mean(grouped.get(group, []))


def _score_mean(rows: list[dict[str, Any]], scores: dict[str, float]) -> float | None:
    return _mean([scores[row["id"]] for row in rows if row["id"] in scores])


def _mean(values: Iterable[float]) -> float | None:
    collected = list(values)
    if not collected:
        return None
    return round(math.fsum(collected) / len(collected), 4)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _judge_model_name() -> str:
    from app.core.config import RAGAS_JUDGE_MODEL

    return RAGAS_JUDGE_MODEL


def _output_path(source: Path, *, in_place: bool) -> Path:
    if in_place:
        return source
    return source.with_name(f"{source.stem}{OUTPUT_SUFFIX}{source.suffix}")


def _write_json_atomic(path: Path, report: dict[str, Any]) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def evaluate_report(
    source: Path,
    *,
    golden_by_id: dict[str, GoldenCase],
    golden_set_path: Path,
    embedding_model: str,
    strictness: int,
    in_place: bool,
    force: bool,
) -> tuple[str, Path | None, float | None]:
    """리포트 하나를 후채점한다. 상태, 저장 경로, 전체 평균을 반환한다."""

    report = _load_report(source)
    if not is_rag_answer_report(report):
        return "답변 없는 검색 전용 리포트", None, None
    if has_answer_relevancy(report) and not force:
        return "이미 측정됨", None, report["overall"]["answer_relevancy"]

    destination = _output_path(source, in_place=in_place)
    if destination.exists() and destination != source and not force:
        return "결과 파일이 이미 있음", destination, None

    samples, empty_response_ids = build_samples(report, golden_by_id)
    scores, usage = score_samples(
        samples,
        embedding_model=embedding_model,
        strictness=strictness,
    )
    scores.update(dict.fromkeys(empty_response_ids, 0.0))

    updated = add_answer_relevancy(
        report,
        scores,
        golden_by_id,
        judge_usage=usage,
        embedding_model=embedding_model,
        strictness=strictness,
        golden_set_path=golden_set_path,
    )
    _write_json_atomic(destination, updated)
    return "완료", destination, updated["overall"]["answer_relevancy"]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        reports = discover_reports(args.paths)
        golden_cases = load_golden_cases(args.golden_set)
        golden_by_id = {case.id: case for case in golden_cases}
        if len(golden_by_id) != len(golden_cases):
            raise ValueError("골든셋에 중복된 사례 id가 있습니다")
        golden_set_path = args.golden_set or _default_golden_set_path()
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    if not reports:
        print("오류: 평가할 JSON 파일이 없습니다", file=sys.stderr)
        return 2

    failed = 0
    completed = 0
    for index, source in enumerate(reports, start=1):
        if not args.quiet:
            print(f"[{index}/{len(reports)}] {source}", file=sys.stderr, flush=True)
        try:
            status, destination, average = evaluate_report(
                source,
                golden_by_id=golden_by_id,
                golden_set_path=golden_set_path,
                embedding_model=args.embedding_model,
                strictness=args.strictness,
                in_place=args.in_place,
                force=args.force,
            )
        except Exception as exc:
            failed += 1
            print(
                f"  실패: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        if status == "완료":
            completed += 1
        if not args.quiet:
            suffix = "" if average is None else f" (평균 {average:.4f})"
            target = "" if destination is None else f" -> {destination}"
            print(f"  {status}{suffix}{target}", file=sys.stderr, flush=True)

    if not args.quiet:
        print(
            f"종료: 완료 {completed}개, 실패 {failed}개, "
            f"건너뜀 {len(reports) - completed - failed}개",
            file=sys.stderr,
        )
    return 1 if failed else 0


def _default_golden_set_path() -> Path:
    from app.services.rag.golden_dataset import golden_dataset_path

    return golden_dataset_path()


if __name__ == "__main__":
    raise SystemExit(main())
