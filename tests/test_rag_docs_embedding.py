"""PDF 페이지 텍스트 정규화 검증.

NUL(0x00)이 섞인 PDF 는 PostgreSQL text 컬럼에 저장되지 않아 적재가 통째로
실패한다(psycopg DataError). 실제로 C11.pdf, P02.pdf 가 이 때문에 적재되지
않고 있었다. 정규화 단계에서 걸러내는 것을 회귀로 고정한다.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.rag.docs_embedding import (
    COMBINE_TEXT_UNDER_N_CHARS,
    EmbeddingCountMismatchError,
    EmptyPdfError,
    MAX_CHARACTERS,
    NEW_AFTER_N_CHARS,
    OVERLAP,
    _chunk_page_number,
    _parse_args,
    get_chunks_from_unstructured_pdf,
    normalize_page_text,
    pdf_has_extractable_text,
    prepare_pdf,
    save_pdf,
)


class NormalizePageTextTest(unittest.TestCase):
    def test_NUL_바이트를_제거한다(self):
        self.assertNotIn("\x00", normalize_page_text("보이스피싱\x00 대응"))

    def test_NUL_자리에서_앞뒤_단어가_붙지_않는다(self):
        # 삭제가 아니라 공백으로 바꿔야 서로 다른 단어가 한 낱말로 합쳐지지 않는다.
        self.assertEqual(normalize_page_text("지급\x00정지"), "지급 정지")

    def test_반복되는_공백과_줄바꿈을_한_칸으로_접는다(self):
        self.assertEqual(
            normalize_page_text("보이스피싱\n\n  피해\t\t신고  "),
            "보이스피싱 피해 신고",
        )

    def test_NUL만_있는_페이지는_빈_문자열이_된다(self):
        # 빈 페이지로 판정되어 청크가 만들어지지 않아야 한다.
        self.assertEqual(normalize_page_text("\x00\x00 \n"), "")

    def test_None과_빈_문자열을_받아도_터지지_않는다(self):
        # pypdf 의 extract_text() 는 None 을 돌려줄 수 있다.
        self.assertEqual(normalize_page_text(None), "")
        self.assertEqual(normalize_page_text(""), "")

    def test_정상_텍스트는_그대로_둔다(self):
        text = "출처가 불분명한 사이트 주소는 클릭을 자제하고 바로 삭제"
        self.assertEqual(normalize_page_text(text), text)


class UnstructuredChunkingTest(unittest.TestCase):
    def _element(self, text, page=1):
        return SimpleNamespace(
            text=text,
            metadata=SimpleNamespace(page_number=page),
        )

    def test_fast_partition과_u1200_옵션을_정확히_전달한다(self):
        elements = [self._element(" 제목\x00 "), self._element("  ")]
        chunks = [self._element("제목\n\n본문", page=1)]
        partitioner = Mock(return_value=elements)
        chunker = Mock(return_value=chunks)

        title, full_text, result = get_chunks_from_unstructured_pdf(
            "/tmp/F01.pdf",
            partitioner=partitioner,
            chunker=chunker,
        )

        self.assertEqual(title, "F01.pdf")
        self.assertEqual(full_text, "제목")
        self.assertEqual(result, [{"page": 1, "content": "제목 본문"}])
        partitioner.assert_called_once_with(
            filename="/tmp/F01.pdf",
            strategy="fast",
            languages=["kor", "eng"],
        )
        chunker.assert_called_once_with(
            [elements[0]],
            max_characters=MAX_CHARACTERS,
            new_after_n_chars=NEW_AFTER_N_CHARS,
            combine_text_under_n_chars=COMBINE_TEXT_UNDER_N_CHARS,
            overlap=OVERLAP,
            overlap_all=False,
            multipage_sections=False,
        )

    def test_orig_elements에서_단일_페이지를_복구한다(self):
        chunk = SimpleNamespace(
            metadata=SimpleNamespace(
                page_number=None,
                orig_elements=[self._element("가", 3), self._element("나", 3)],
            )
        )
        self.assertEqual(_chunk_page_number(chunk), 3)

    def test_페이지를_넘는_청크는_거부한다(self):
        chunk = SimpleNamespace(
            metadata=SimpleNamespace(
                page_number=None,
                orig_elements=[self._element("가", 1), self._element("나", 2)],
            )
        )
        with self.assertRaisesRegex(ValueError, "페이지 경계를 넘는"):
            _chunk_page_number(chunk)

    @patch("pypdf.PdfReader")
    def test_텍스트_레이어가_없는_PDF를_찾는다(self, reader):
        reader.return_value.pages = [
            SimpleNamespace(extract_text=Mock(return_value=None)),
            SimpleNamespace(extract_text=Mock(return_value="\x00  ")),
        ]
        self.assertFalse(pdf_has_extractable_text("C01.pdf"))

    @patch("app.services.rag.docs_embedding.pdf_has_extractable_text")
    def test_이미지형_PDF는_Unstructured를_호출하지_않는다(self, has_text):
        has_text.return_value = False
        with patch.dict("sys.modules", {}):
            title, full_text, chunks = get_chunks_from_unstructured_pdf("C01.pdf")
        self.assertEqual((title, full_text, chunks), ("C01.pdf", "", []))


class PdfPreparationTest(unittest.TestCase):
    @patch("app.services.rag.docs_embedding.get_chunks_from_unstructured_pdf")
    def test_빈_결과는_임베딩하지_않고_실패한다(self, get_chunks):
        get_chunks.return_value = ("C01.pdf", "", [])
        embedding_fn = Mock()

        with self.assertRaises(EmptyPdfError):
            prepare_pdf(
                "C01.pdf",
                embedding_fn=embedding_fn,
            )

        embedding_fn.assert_not_called()

    @patch("app.services.rag.docs_embedding.get_chunks_from_unstructured_pdf")
    def test_임베딩_개수_불일치는_DB_교체_전에_실패한다(self, get_chunks):
        get_chunks.return_value = (
            "F01.pdf",
            "전체",
            [{"page": 1, "content": "가"}, {"page": 2, "content": "나"}],
        )

        with self.assertRaises(EmbeddingCountMismatchError):
            prepare_pdf(
                "F01.pdf",
                embedding_fn=lambda _texts: [[0.1]],
            )

    @patch("app.services.rag.docs_embedding.Session")
    @patch("app.services.rag.docs_embedding.prepare_pdf")
    def test_준비_실패시_DB_세션조차_열지_않는다(self, prepare, session):
        prepare.side_effect = RuntimeError("embedding failed")

        with self.assertRaisesRegex(RuntimeError, "embedding failed"):
            save_pdf("F01.pdf")

        session.assert_not_called()


class IndexingCliTest(unittest.TestCase):
    def test_기본_PDF_디렉터리를_사용한다(self):
        args = _parse_args([])
        self.assertTrue(args.target_dir.endswith("docs/embed_target_pdfs"))

    def test_PDF_디렉터리를_지정할_수_있다(self):
        args = _parse_args(["/tmp/pdfs"])
        self.assertEqual(args.target_dir, "/tmp/pdfs")


if __name__ == "__main__":
    unittest.main()
