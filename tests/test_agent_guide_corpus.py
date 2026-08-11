import unittest

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.services.agent.guide_corpus import (
    ALLOWED_SOURCE_TYPES,
    DEFAULT_CORPUS_ROOT,
    OFFICIAL_GUIDE,
    load_guide_corpus,
)


class AgentGuideCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = load_guide_corpus(DEFAULT_CORPUS_ROOT)

    def test_initial_corpus_has_official_and_internal_documents(self) -> None:
        self.assertGreaterEqual(len(self.documents), 7)
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


if __name__ == "__main__":
    unittest.main()
