import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.domain.agent_guide import GuideDocumentValidationError
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.services.agent.guide_corpus import DEFAULT_CORPUS_ROOT, load_guide_corpus
from app.services.agent.guide_evaluation import (
    DEFAULT_GUIDE_EVALUATION_PATH,
    GuideSearchEvaluationReport,
    RetrievalMetrics,
    load_guide_evaluation_cases,
)
from app.services.agent.response_policy import (
    DEFAULT_POLICY_PATH,
    load_response_policies,
)


class AgentGuideEvaluationTest(unittest.TestCase):
    """검색 구현 전에도 평가 세트와 문서 계약의 연결을 검증한다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_guide_evaluation_cases(DEFAULT_GUIDE_EVALUATION_PATH)
        cls.documents = load_guide_corpus(DEFAULT_CORPUS_ROOT)

    def test_evaluation_has_five_queries_for_each_fraud_type(self) -> None:
        counts = Counter(case.fraud_type for case in self.cases)

        self.assertEqual(len(self.cases), 20)
        self.assertEqual(set(counts), FINAL_FRAUD_TYPE_CODES)
        self.assertTrue(all(count == 5 for count in counts.values()))

    def test_expected_documents_exist_and_match_query_filters(self) -> None:
        documents = {document.document_id: document for document in self.documents}

        for case in self.cases:
            for document_id in case.expected_document_ids:
                with self.subTest(query_id=case.query_id, document_id=document_id):
                    document = documents[document_id]
                    self.assertIn(case.fraud_type, document.fraud_types)
                    self.assertIn(case.audience, document.audiences)
                    self.assertIn(case.risk_grade, document.risk_grades)
                    self.assertTrue(
                        set(case.action_codes).intersection(document.action_codes)
                    )

    def test_evaluation_action_codes_exist_in_policy(self) -> None:
        policy_action_codes = {
            action.action_code
            for policy in load_response_policies(DEFAULT_POLICY_PATH)
            for action in policy.actions
        }

        for case in self.cases:
            with self.subTest(query_id=case.query_id):
                self.assertTrue(set(case.action_codes).issubset(policy_action_codes))

    def test_duplicate_query_id_is_rejected(self) -> None:
        invalid_yaml = """cases:
  - query_id: DUPLICATE
    query: 첫 번째 질문
    fraud_type: VOICE_PHISHING
    audience: MONITORING
    risk_grade: HIGH
    action_codes: [VERIFY_CUSTOMER_TRANSACTION]
    expected_document_ids: [DOC-001]
  - query_id: DUPLICATE
    query: 두 번째 질문
    fraud_type: VOICE_PHISHING
    audience: MONITORING
    risk_grade: HIGH
    action_codes: [VERIFY_CUSTOMER_TRANSACTION]
    expected_document_ids: [DOC-002]
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.yaml"
            path.write_text(invalid_yaml, encoding="utf-8")

            with self.assertRaisesRegex(
                GuideDocumentValidationError,
                "query_id가 중복",
            ):
                load_guide_evaluation_cases(path)

    def test_report_can_be_flattened_to_csv_rows(self) -> None:
        metrics = RetrievalMetrics(2, 0.5, 1.0, 1.0, 0.75)
        rows = GuideSearchEvaluationReport(
            baseline=metrics,
            filtered=metrics,
            by_fraud_type={"VOICE_PHISHING": {"baseline": metrics}},
        ).to_csv_rows()

        self.assertEqual(rows[0]["scope"], "ALL")
        self.assertEqual(rows[1]["strategy"], "FILTERED")
        self.assertEqual(rows[2]["scope"], "VOICE_PHISHING")


if __name__ == "__main__":
    unittest.main()
