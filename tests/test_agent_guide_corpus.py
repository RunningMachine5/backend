import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


CORPUS_ROOT = Path(__file__).resolve().parents[1] / "docs" / "agent_guides"
DOCUMENT_ROOTS = (CORPUS_ROOT / "official", CORPUS_ROOT / "internal_demo")
REQUIRED_FIELDS = {
    "document_id",
    "title",
    "source_type",
    "source_name",
    "source_url",
    "fraud_types",
    "audiences",
    "topics",
    "published_at",
    "accessed_at",
}
ALLOWED_SOURCE_TYPES = {"OFFICIAL_GUIDE", "INTERNAL_DEMO_GUIDE"}
ALLOWED_AUDIENCES = {"MONITORING", "CUSTOMER", "COMMON"}
ALLOWED_TOPICS = {
    "CUSTOMER_CONFIRMATION",
    "SECURITY_CHECK",
    "RECIPIENT_ACCOUNT_REVIEW",
    "ADDITIONAL_TRANSACTION_REVIEW",
    "ACCOUNT_FLOW_REVIEW",
    "DAMAGE_REPORT",
    "MESSENGER_IDENTITY_CHECK",
    "EMERGENCY_RESPONSE",
    "MANUAL_REVIEW",
}
OFFICIAL_SOURCE_HOSTS = {
    "www.fss.or.kr",
    "www.fsec.or.kr",
    "www.fsc.go.kr",
    "ecrm.police.go.kr",
}


def load_document(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"Front matter 시작 구분자가 없다: {path}")
    try:
        raw_metadata, body = text[4:].split("\n---\n", maxsplit=1)
    except ValueError as error:
        raise AssertionError(f"Front matter 종료 구분자가 없다: {path}") from error

    metadata = yaml.safe_load(raw_metadata)
    if not isinstance(metadata, dict):
        raise AssertionError(f"Front matter가 객체 형식이 아니다: {path}")
    return metadata, body.strip()


class AgentGuideCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(
            path
            for root in DOCUMENT_ROOTS
            for path in root.glob("*.md")
        )
        cls.documents = [
            (path, *load_document(path))
            for path in cls.paths
        ]

    def test_initial_corpus_has_official_and_internal_documents(self) -> None:
        self.assertGreaterEqual(len(self.documents), 7)
        source_types = {
            metadata["source_type"] for _, metadata, _ in self.documents
        }
        self.assertEqual(source_types, ALLOWED_SOURCE_TYPES)

    def test_document_ids_are_unique(self) -> None:
        document_ids = [
            metadata["document_id"] for _, metadata, _ in self.documents
        ]
        self.assertEqual(len(document_ids), len(set(document_ids)))

    def test_required_metadata_and_body_are_present(self) -> None:
        for path, metadata, body in self.documents:
            with self.subTest(path=path.name):
                self.assertTrue(REQUIRED_FIELDS.issubset(metadata))
                self.assertIsInstance(metadata["document_id"], str)
                self.assertTrue(metadata["document_id"].strip())
                self.assertIsInstance(metadata["title"], str)
                self.assertTrue(metadata["title"].strip())
                self.assertIsInstance(metadata["source_name"], str)
                self.assertTrue(metadata["source_name"].strip())
                self.assertTrue(body.startswith("# "))
                self.assertIn("\n## ", body)

    def test_metadata_codes_are_supported(self) -> None:
        for path, metadata, _ in self.documents:
            with self.subTest(path=path.name):
                self.assertIn(metadata["source_type"], ALLOWED_SOURCE_TYPES)
                self.assertTrue(metadata["fraud_types"])
                self.assertTrue(set(metadata["fraud_types"]).issubset(FINAL_FRAUD_TYPE_CODES))
                self.assertTrue(metadata["audiences"])
                self.assertTrue(set(metadata["audiences"]).issubset(ALLOWED_AUDIENCES))
                self.assertTrue(metadata["topics"])
                self.assertTrue(set(metadata["topics"]).issubset(ALLOWED_TOPICS))

    def test_source_metadata_matches_document_type(self) -> None:
        for path, metadata, _ in self.documents:
            with self.subTest(path=path.name):
                if metadata["source_type"] == "OFFICIAL_GUIDE":
                    self.assertIsInstance(metadata["source_url"], str)
                    parsed = urlparse(metadata["source_url"])
                    self.assertEqual(parsed.scheme, "https")
                    self.assertIn(parsed.hostname, OFFICIAL_SOURCE_HOSTS)
                else:
                    self.assertIsNone(metadata["source_url"])
                    self.assertEqual(
                        metadata["source_name"],
                        "FDShield 시연용 내부 지침",
                    )

    def test_document_dates_are_explicit(self) -> None:
        for path, metadata, _ in self.documents:
            with self.subTest(path=path.name):
                self.assertTrue(
                    metadata["published_at"] is None
                    or isinstance(metadata["published_at"], date)
                )
                self.assertIsInstance(metadata["accessed_at"], date)

    def test_official_corpus_covers_all_fraud_types(self) -> None:
        covered_types = {
            fraud_type
            for _, metadata, _ in self.documents
            if metadata["source_type"] == "OFFICIAL_GUIDE"
            for fraud_type in metadata["fraud_types"]
        }
        self.assertEqual(covered_types, FINAL_FRAUD_TYPE_CODES)


if __name__ == "__main__":
    unittest.main()
