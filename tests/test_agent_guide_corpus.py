import unittest

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.services.agent.guide_corpus import (
    ALLOWED_SOURCE_TYPES,
    DEFAULT_CORPUS_ROOT,
    INTERNAL_DEMO_GUIDE,
    OFFICIAL_GUIDE,
    load_guide_corpus,
)
from app.services.agent.response_policy import (
    DEFAULT_POLICY_PATH,
    load_response_policies,
)


class AgentGuideCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = load_guide_corpus(DEFAULT_CORPUS_ROOT)

    def test_initial_corpus_has_official_and_internal_documents(self) -> None:
        self.assertGreaterEqual(len(self.documents), 16)
        source_types = {document.source_type for document in self.documents}
        self.assertEqual(source_types, ALLOWED_SOURCE_TYPES)

    def test_documents_have_searchable_heading_structure(self) -> None:
        for document in self.documents:
            with self.subTest(path=document.source_path.name):
                self.assertTrue(document.content.startswith("# "))
                self.assertIn("\n## ", document.content)

    def test_official_corpus_covers_all_fraud_types(self) -> None:
        covered_types = {
            fraud_type
            for document in self.documents
            if document.source_type == OFFICIAL_GUIDE
            for fraud_type in document.fraud_types
        }
        self.assertEqual(covered_types, FINAL_FRAUD_TYPE_CODES)

    def test_internal_corpus_covers_every_type_and_audience(self) -> None:
        """각 유형에 담당자용 절차와 고객용 안내가 모두 존재해야 한다."""

        covered_pairs = {
            (fraud_type, audience)
            for document in self.documents
            if document.source_type == INTERNAL_DEMO_GUIDE
            for fraud_type in document.fraud_types
            for audience in document.audiences
        }
        for fraud_type in FINAL_FRAUD_TYPE_CODES:
            with self.subTest(fraud_type=fraud_type):
                self.assertIn((fraud_type, "MONITORING"), covered_pairs)
                self.assertIn((fraud_type, "CUSTOMER"), covered_pairs)

    def test_document_action_codes_match_and_cover_response_policy(self) -> None:
        policies = load_response_policies(DEFAULT_POLICY_PATH)
        policy_action_codes = {
            action.action_code
            for policy in policies
            for action in policy.actions
        }
        document_action_codes = {
            action_code
            for document in self.documents
            for action_code in document.action_codes
        }

        self.assertTrue(document_action_codes.issubset(policy_action_codes))
        self.assertEqual(document_action_codes, policy_action_codes)

    def test_documents_define_risk_grade_action_and_version_metadata(self) -> None:
        for document in self.documents:
            with self.subTest(document_id=document.document_id):
                self.assertTrue(document.risk_grades)
                self.assertTrue(document.action_codes)
                self.assertTrue(document.version)


if __name__ == "__main__":
    unittest.main()
