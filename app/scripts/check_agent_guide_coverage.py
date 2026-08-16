"""내부 정책 조치별 대응 가이드 커버리지를 표로 출력한다."""

from app.services.agent.guide_corpus import load_guide_corpus
from app.services.agent.guide_coverage import analyze_guide_coverage
from app.services.agent.response_policy import get_default_policy_repository


def main() -> None:
    policies = get_default_policy_repository().policies
    documents = load_guide_corpus()
    report = analyze_guide_coverage(policies, documents)

    print(
        "fraud_type\trisk_grade\taudience\taction_code\t"
        "matching_document_count\tstatus"
    )
    for row in report.rows:
        print(
            f"{row.fraud_type}\t{row.risk_grade}\t{row.audience}\t"
            f"{row.action_code}\t{row.matching_document_count}\t{row.status}"
        )

    print(
        "\nsummary: "
        f"total={len(report.rows)}, covered={report.covered_count}, "
        f"missing={report.missing_count}, "
        f"coverage_rate={report.coverage_rate:.2%}"
    )
    if report.broad_document_ids:
        print("broad_documents: " + ", ".join(report.broad_document_ids))

    if report.missing_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
