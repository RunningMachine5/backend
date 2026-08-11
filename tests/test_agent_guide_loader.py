import tempfile
import unittest
from pathlib import Path

from app.domain.agent_guide import (
    DuplicateGuideDocumentError,
    GuideChunkingError,
    GuideCorpusError,
    GuideDocument,
    GuideDocumentParseError,
    GuideDocumentValidationError,
)
from app.services.agent.guide_corpus import (
    DEFAULT_CORPUS_ROOT,
    create_guide_chunks,
    discover_guide_paths,
    load_and_chunk_guide_corpus,
    load_guide_corpus,
    load_guide_document,
)


def _valid_document(
    *,
    document_id: str = "TEST-GUIDE-001",
    body: str | None = None,
) -> str:
    document_body = body or """# 테스트 대응 가이드

## 고객 확인

등록된 연락처로 고객에게 본인 거래 여부를 확인한다.

## 담당자 주의사항

인증번호와 OTP 번호를 요구하지 않는다.
"""
    return f"""---
document_id: {document_id}
title: 테스트 대응 가이드
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - VOICE_PHISHING
audiences:
  - MONITORING
topics:
  - CUSTOMER_CONFIRMATION
published_at: null
accessed_at: 2026-08-10
---

{document_body}
"""


class GuideDocumentLoaderTest(unittest.TestCase):
    def test_discovers_only_search_documents_in_stable_order(self) -> None:
        paths = discover_guide_paths(DEFAULT_CORPUS_ROOT)

        self.assertGreaterEqual(len(paths), 7)
        self.assertEqual(paths, tuple(sorted(paths, key=lambda path: path.as_posix())))
        self.assertNotIn("README.md", {path.name for path in paths})

    def test_loads_current_corpus_as_immutable_documents(self) -> None:
        documents = load_guide_corpus(DEFAULT_CORPUS_ROOT)

        self.assertGreaterEqual(len(documents), 7)
        self.assertTrue(all(isinstance(document, GuideDocument) for document in documents))
        self.assertTrue(all(document.source_path.is_absolute() for document in documents))

    def test_parses_windows_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text(
                _valid_document().replace("\n", "\r\n"),
                encoding="utf-8",
            )

            document = load_guide_document(path)

        self.assertEqual(document.document_id, "TEST-GUIDE-001")
        self.assertEqual(document.fraud_types, ("VOICE_PHISHING",))
        self.assertTrue(document.content.startswith("# 테스트 대응 가이드"))

    def test_rejects_missing_front_matter_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("# 본문만 있는 문서", encoding="utf-8")

            with self.assertRaises(GuideDocumentParseError):
                load_guide_document(path)

            path.write_text("---\ndocument_id: TEST\n", encoding="utf-8")
            with self.assertRaises(GuideDocumentParseError):
                load_guide_document(path)

    def test_rejects_malformed_yaml_and_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("---\ntitle: [\n---\n# 본문", encoding="utf-8")
            with self.assertRaises(GuideDocumentParseError):
                load_guide_document(path)

            path.write_text(
                _valid_document().replace("title: 테스트 대응 가이드\n", ""),
                encoding="utf-8",
            )
            with self.assertRaises(GuideDocumentValidationError):
                load_guide_document(path)

    def test_rejects_unsupported_codes_and_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text(
                _valid_document().replace("VOICE_PHISHING", "UNKNOWN_TYPE"),
                encoding="utf-8",
            )
            with self.assertRaises(GuideDocumentValidationError):
                load_guide_document(path)

            empty_body = _valid_document(body="   \n")
            path.write_text(empty_body, encoding="utf-8")
            with self.assertRaises(GuideDocumentValidationError):
                load_guide_document(path)

    def test_rejects_duplicate_document_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "official"
            internal = root / "internal_demo"
            official.mkdir()
            internal.mkdir()
            (internal / "first.md").write_text(_valid_document(), encoding="utf-8")
            (internal / "second.md").write_text(_valid_document(), encoding="utf-8")

            with self.assertRaises(DuplicateGuideDocumentError):
                load_guide_corpus(root)

    def test_rejects_source_type_that_does_not_match_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "official"
            internal = root / "internal_demo"
            official.mkdir()
            internal.mkdir()
            (official / "wrong.md").write_text(
                _valid_document(),
                encoding="utf-8",
            )
            (internal / "valid.md").write_text(
                _valid_document(document_id="TEST-GUIDE-002"),
                encoding="utf-8",
            )

            with self.assertRaises(GuideDocumentValidationError):
                load_guide_corpus(root)

    def test_rejects_missing_document_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(GuideCorpusError):
                discover_guide_paths(directory)


class GuideChunkerTest(unittest.TestCase):
    def test_splits_h2_sections_and_preserves_nested_headings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text(
                _valid_document(
                    body="""# 테스트 대응 가이드

문서 전체에 적용되는 소개이다.

## 고객 확인

고객에게 본인 거래 여부를 확인한다.

### 확인 방법

공식 연락처를 사용한다.

## 주의사항

인증번호를 요구하지 않는다.
"""
                ),
                encoding="utf-8",
            )
            document = load_guide_document(path)

            chunks = create_guide_chunks(document)

        self.assertEqual([chunk.chunk_index for chunk in chunks], [0, 1, 2])
        self.assertEqual(
            [chunk.heading for chunk in chunks],
            ["테스트 대응 가이드", "고객 확인", "주의사항"],
        )
        self.assertIn("### 확인 방법", chunks[1].content)

    def test_inherits_document_metadata_to_every_chunk(self) -> None:
        document = load_guide_corpus(DEFAULT_CORPUS_ROOT)[0]

        chunks = create_guide_chunks(document)

        for chunk in chunks:
            with self.subTest(chunk_index=chunk.chunk_index):
                self.assertEqual(chunk.document_id, document.document_id)
                self.assertEqual(chunk.document_title, document.title)
                self.assertEqual(chunk.source_type, document.source_type)
                self.assertEqual(chunk.fraud_types, document.fraud_types)
                self.assertEqual(chunk.audiences, document.audiences)
                self.assertEqual(chunk.topics, document.topics)

    def test_skips_empty_sections_and_uses_contiguous_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text(
                _valid_document(
                    body="""# 테스트 대응 가이드

## 비어 있는 섹션

## 내용이 있는 섹션

확인할 내용이다.
"""
                ),
                encoding="utf-8",
            )
            chunks = create_guide_chunks(load_guide_document(path))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertEqual(chunks[0].heading, "내용이 있는 섹션")

    def test_rejects_document_without_searchable_content(self) -> None:
        document = load_guide_corpus(DEFAULT_CORPUS_ROOT)[0]
        empty_document = GuideDocument(
            document_id=document.document_id,
            title=document.title,
            source_type=document.source_type,
            source_name=document.source_name,
            source_url=document.source_url,
            fraud_types=document.fraud_types,
            audiences=document.audiences,
            topics=document.topics,
            published_at=document.published_at,
            accessed_at=document.accessed_at,
            content="# 제목\n\n## 빈 섹션",
            source_path=document.source_path,
        )

        with self.assertRaises(GuideChunkingError):
            create_guide_chunks(empty_document)

    def test_full_corpus_chunking_is_reproducible(self) -> None:
        first = load_and_chunk_guide_corpus(DEFAULT_CORPUS_ROOT)
        second = load_and_chunk_guide_corpus(DEFAULT_CORPUS_ROOT)

        self.assertEqual(first, second)
        self.assertGreater(len(first), len(load_guide_corpus(DEFAULT_CORPUS_ROOT)))


if __name__ == "__main__":
    unittest.main()
