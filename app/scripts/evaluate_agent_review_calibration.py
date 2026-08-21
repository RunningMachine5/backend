"""담당자 검토 결과를 이용해 Agent 임계값·유사도 후보를 비교한다."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session, select  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.data.model.agent import AgentCase, AgentReview  # noqa: E402
from app.data.model.fraud_rule import FraudTypeScoreResult  # noqa: E402
from app.services.agent.review_calibration import (  # noqa: E402
    ReviewedCase,
    evaluate_review_calibration,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--csv-output", type=Path)
    parser.add_argument(
        "--exclude-seed",
        action="store_true",
        help="시연용 유사 완료 사건을 제외하고 실제 담당자 검토 사건만 평가한다.",
    )
    args = parser.parse_args()

    with Session(engine) as session:
        reviewed_cases = _load_reviewed_cases(session, exclude_seed=args.exclude_seed)
    results = evaluate_review_calibration(reviewed_cases)
    payload = {
        "note": "후보 설정은 자동 적용하지 않으며, 담당자 검토 후 선택한다.",
        "exclude_seed": args.exclude_seed,
        "reviewed_case_count": len(reviewed_cases),
        "results": [result.to_dict() for result in results],
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    if args.csv_output:
        rows = [result.to_csv_row() for result in results]
        args.csv_output.parent.mkdir(parents=True, exist_ok=True)
        with args.csv_output.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def _load_reviewed_cases(
    session: Session,
    *,
    exclude_seed: bool,
) -> list[ReviewedCase]:
    statement = (
        select(AgentCase, FraudTypeScoreResult, AgentReview)
        .join(
            FraudTypeScoreResult,
            FraudTypeScoreResult.id == AgentCase.fraud_type_score_result_id,
        )
        .join(AgentReview, AgentReview.case_id == AgentCase.case_id)
    )
    rows = session.exec(statement).all()
    if exclude_seed:
        rows = [
            row
            for row in rows
            if not (row[0].generation_metadata or {}).get("seed_data", False)
        ]
    return [
        ReviewedCase(
            case_id=agent_case.case_id,
            type_scores=score_result.type_scores,
            matched_components=score_result.matched_components,
            risk_score=agent_case.risk_score,
            risk_grade=agent_case.risk_grade,
            decision=review.decision,
            confirmed_fraud_type=review.confirmed_fraud_type,
        )
        for agent_case, score_result, review in rows
    ]


if __name__ == "__main__":
    main()
