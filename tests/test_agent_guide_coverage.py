import unittest

from app.services.agent.guide_corpus import load_guide_corpus
from app.services.agent.guide_coverage import MISSING, analyze_guide_coverage
from app.services.agent.response_policy import get_default_policy_repository


class AgentGuideCoverageTest(unittest.TestCase):
    def test_default_corpus_covers_every_monitoring_policy_action(self) -> None:
        report = analyze_guide_coverage(
            get_default_policy_repository().policies,
            load_guide_corpus(),
        )

        self.assertEqual(len(report.rows), 32)
        self.assertEqual(report.missing_count, 0)
        self.assertEqual(report.coverage_rate, 1.0)

    def test_missing_documents_are_reported_per_policy_combination(self) -> None:
        policy = get_default_policy_repository().policies[0]

        report = analyze_guide_coverage([policy], [])

        self.assertEqual(len(report.rows), len(policy.actions))
        self.assertTrue(all(row.status == MISSING for row in report.rows))

    def test_documents_covering_many_types_are_reported_for_review(self) -> None:
        report = analyze_guide_coverage(
            get_default_policy_repository().policies,
            load_guide_corpus(),
        )

        self.assertIn(
            "FDS-INTERNAL-HIGH-RISK-EMERGENCY-001",
            report.broad_document_ids,
        )


if __name__ == "__main__":
    unittest.main()
