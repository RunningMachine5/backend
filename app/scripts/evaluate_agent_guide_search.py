"""고정 평가 질의로 대응 가이드 검색 품질 개선 전후를 측정한다."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.repositories.agent_guide import AgentGuideRepository  # noqa: E402
from app.services.agent.guide_embedder import OpenAIGuideEmbedder  # noqa: E402
from app.services.agent.guide_evaluation import (  # noqa: E402
    evaluate_guide_search,
    load_guide_evaluation_cases,
)
from app.services.agent.guide_search import GuideSearchService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        help="검색 평가 보고서를 저장할 JSON 파일 경로이다.",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        help="검색 평가 요약을 저장할 CSV 파일 경로이다.",
    )
    args = parser.parse_args()

    with Session(engine) as session:
        service = GuideSearchService(
            AgentGuideRepository(session),
            OpenAIGuideEmbedder(),
        )
        report = evaluate_guide_search(load_guide_evaluation_cases(), service)
    output = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    print(output)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    if args.csv_output is not None:
        rows = report.to_csv_rows()
        args.csv_output.parent.mkdir(parents=True, exist_ok=True)
        with args.csv_output.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
