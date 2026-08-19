"""RAG 평가 골든셋(evals/rag/datasets/golden_v1.jsonl) 구조와 근거를 검증한다.

LLM·DB·네트워크를 부르지 않는다. PDF 원문 대조는 로컬 코퍼스가 있을 때만 돈다.
"""

import collections
import os
import unittest

from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.rag.golden_dataset import (
    BEHAVIOR_ROLE,
    CATEGORIES,
    GUIDE_ROLE,
    load_golden_cases,
)

# 요구 구성: 정상 85건(쉬움 55 + 어려움 20 + 다문서 10) + 특수 15건(무근거 10 + 경계 5)
EXPECTED_COUNTS = {
    "easy": 55,
    "hard": 20,
    "multi_doc": 10,
    "unanswerable": 10,
    "page_boundary": 5,
}
TOTAL = 100

# 텍스트가 전혀 추출되지 않는 이미지형 PDF 라 근거로 쓸 수 없다.
IMAGE_ONLY_DOCUMENT = "C01"
# 평가 코퍼스의 PDF 43개 중 이미지형 C01 을 뺀 42개를 모두 인용해야 한다.
EXPECTED_DOCUMENT_COUNT = 42
# 한 문서에 질문이 몰리지 않도록 두는 상한
MAX_CITATIONS_PER_DOCUMENT = 6

# (문서, 고객행동 페이지, 대응가이드 페이지) — 페이지 단위 청킹이 깨지는 지점
PAGE_BOUNDARY_PAIRS = {
    ("C02", 1, 2),
    ("F03", 2, 3),
    ("F17", 2, 3),
    ("P10", 3, 4),
    ("P11", 2, 3),
}

# 적재 대상 디렉터리와 같은 곳을 본다. 이 디렉터리는 커밋되지 않으므로,
# PDF 가 없는 환경에서는 아래 대조 테스트만 건너뛴다.
CORPUS_DIR = os.getenv(
    "RAG_CORPUS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "embed_target_pdfs"),
)


class GoldenDatasetStructureTest(unittest.TestCase):
    """수량·카테고리·기대동작 같은 구조 계약."""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_golden_cases()

    def test_총_100건이고_id가_중복되지_않는다(self):
        self.assertEqual(len(self.cases), TOTAL)
        ids = [case.id for case in self.cases]
        self.assertEqual(len(set(ids)), TOTAL)

    def test_카테고리별_건수가_요구_구성과_같다(self):
        counts = collections.Counter(case.category for case in self.cases)
        self.assertEqual(dict(counts), EXPECTED_COUNTS)

    def test_알려진_카테고리만_쓴다(self):
        for case in self.cases:
            self.assertIn(case.category, CATEGORIES, case.id)

    def test_난이도는_쉬움만_EASY이고_나머지는_HARD다(self):
        for case in self.cases:
            expected = "EASY" if case.category == "easy" else "HARD"
            self.assertEqual(case.difficulty, expected, case.id)

    def test_무근거_사례는_근거가_없고_기권_문구를_정답으로_쓴다(self):
        rows = [c for c in self.cases if c.category == "unanswerable"]
        self.assertEqual(len(rows), EXPECTED_COUNTS["unanswerable"])
        for case in rows:
            self.assertEqual(case.reference_contexts, (), case.id)
            self.assertEqual(case.expected_behavior, "ABSTAIN", case.id)
            self.assertTrue(case.is_abstain, case.id)
            # reference 는 "근거가 없음을 밝힌다"는 기대 동작을 문서화하는 값이다.
            # 문구를 새로 만들지 않고 messages.md B.5 원문을 그대로 쓴다. 채점에는
            # 쓰이지 않는다 — 질의 0건이면 파이프라인이 빈 문자열을 내고 검색 0건일
            # 때만 이 문구를 내므로, 정답 응답이 둘이라 문자열 비교가 성립하지 않는다.
            self.assertEqual(
                case.reference, UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE, case.id
            )

    def test_답변_가능_사례는_근거가_하나_이상이다(self):
        for case in self.cases:
            if case.category == "unanswerable":
                continue
            self.assertEqual(case.expected_behavior, "ANSWER", case.id)
            self.assertGreaterEqual(len(case.reference_contexts), 1, case.id)

    def test_다문서_사례는_서로_다른_문서_둘_이상을_근거로_쓴다(self):
        rows = [c for c in self.cases if c.category == "multi_doc"]
        for case in rows:
            documents = {c.document_id for c in case.reference_contexts}
            self.assertGreaterEqual(len(documents), 2, case.id)

    def test_모든_사례에_필수_주장과_금지_주장이_있다(self):
        for case in self.cases:
            self.assertGreaterEqual(len(case.required_claims), 1, case.id)
            self.assertGreaterEqual(len(case.forbidden_claims), 1, case.id)
            self.assertTrue(case.user_input.strip(), case.id)
            self.assertTrue(case.reference.strip(), case.id)


class PageBoundaryCaseTest(unittest.TestCase):
    """앞 페이지=고객행동, 뒤 페이지=대응가이드인 5건."""

    @classmethod
    def setUpClass(cls):
        cls.rows = [
            c for c in load_golden_cases() if c.category == "page_boundary"
        ]

    def test_지정된_문서_페이지_쌍과_정확히_일치한다(self):
        pairs = set()
        for case in self.rows:
            guide = [
                c for c in case.reference_contexts if c.context_role == GUIDE_ROLE
            ]
            behavior = [
                c for c in case.reference_contexts if c.context_role == BEHAVIOR_ROLE
            ]
            self.assertEqual(len(guide), 1, case.id)
            self.assertEqual(len(behavior), 1, case.id)
            self.assertEqual(guide[0].document_id, behavior[0].document_id, case.id)
            pairs.add((guide[0].document_id, behavior[0].page, guide[0].page))
        self.assertEqual(pairs, PAGE_BOUNDARY_PAIRS)

    def test_대응가이드는_고객행동_바로_다음_페이지다(self):
        for case in self.rows:
            guide = next(
                c for c in case.reference_contexts if c.context_role == GUIDE_ROLE
            )
            behavior = next(
                c for c in case.reference_contexts if c.context_role == BEHAVIOR_ROLE
            )
            self.assertEqual(guide.page, behavior.page + 1, case.id)

    def test_경계_사례만_behavior_역할을_쓴다(self):
        boundary_ids = {case.id for case in self.rows}
        for case in load_golden_cases():
            if case.id in boundary_ids:
                continue
            roles = {c.context_role for c in case.reference_contexts}
            self.assertNotIn(BEHAVIOR_ROLE, roles, case.id)


class DocumentCoverageTest(unittest.TestCase):
    """문서 커버리지: 43개 전부 인용하고 한 문서에 몰리지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_golden_cases()
        cls.citations = collections.Counter(
            context.document_id
            for case in cls.cases
            for context in case.reference_contexts
        )

    def test_42개_문서를_모두_인용한다(self):
        self.assertEqual(len(self.citations), EXPECTED_DOCUMENT_COUNT)

    def test_이미지형_PDF는_근거로_쓰지_않는다(self):
        self.assertNotIn(IMAGE_ONLY_DOCUMENT, self.citations)

    def test_한_문서_인용이_상한을_넘지_않는다(self):
        over = {d: n for d, n in self.citations.items() if n > MAX_CITATIONS_PER_DOCUMENT}
        self.assertEqual(over, {})

    def test_파일명은_문서ID에_대응한다(self):
        for case in self.cases:
            for context in case.reference_contexts:
                self.assertEqual(
                    context.filename, f"{context.document_id}.pdf", case.id
                )
                self.assertGreaterEqual(context.page, 1, case.id)


class RagasFieldMappingTest(unittest.TestCase):
    """로더가 RAGAS 입력 필드로 올바르게 변환하는지."""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_golden_cases()

    def test_RAGAS_필드로_변환된다(self):
        case = next(c for c in self.cases if c.category == "multi_doc")
        fields = case.to_ragas_fields()
        self.assertEqual(set(fields), {"user_input", "reference", "reference_contexts"})
        self.assertEqual(fields["user_input"], case.user_input)
        self.assertEqual(fields["reference"], case.reference)
        self.assertEqual(
            fields["reference_contexts"],
            [c.evidence for c in case.reference_contexts],
        )
        self.assertTrue(all(isinstance(c, str) for c in fields["reference_contexts"]))

    def test_무근거_사례의_reference_contexts는_빈_리스트다(self):
        case = next(c for c in self.cases if c.category == "unanswerable")
        self.assertEqual(case.to_ragas_fields()["reference_contexts"], [])

    def test_locators는_파일명과_페이지_쌍을_돌려준다(self):
        case = next(c for c in self.cases if c.category == "page_boundary")
        self.assertEqual(len(case.locators()), 2)
        self.assertTrue(all(isinstance(p, int) for _, p in case.locators()))


@unittest.skipUnless(
    os.path.isdir(CORPUS_DIR),
    f"평가 코퍼스가 없어 PDF 대조를 건너뜁니다: {CORPUS_DIR}",
)
class EvidenceAgainstPdfTest(unittest.TestCase):
    """모든 evidence 가 실제 PDF 페이지 원문의 부분 문자열인지 대조한다."""

    @classmethod
    def setUpClass(cls):
        from pypdf import PdfReader

        from app.services.rag.docs_embedding import normalize_page_text

        cls.cases = load_golden_cases()
        cls.pages = {}
        needed = {
            context.document_id
            for case in cls.cases
            for context in case.reference_contexts
        }
        for document_id in sorted(needed):
            reader = PdfReader(os.path.join(CORPUS_DIR, f"{document_id}.pdf"))
            cls.pages[document_id] = [
                # 적재 코드와 같은 정규화 함수를 그대로 쓴다.
                normalize_page_text(page.extract_text())
                for page in reader.pages
            ]

    def test_모든_근거가_해당_PDF_페이지에_그대로_있다(self):
        for case in self.cases:
            for context in case.reference_contexts:
                pages = self.pages[context.document_id]
                self.assertLessEqual(context.page, len(pages), case.id)
                page_text = pages[context.page - 1]
                self.assertIn(
                    context.evidence,
                    page_text,
                    f"{case.id}: {context.document_id} p{context.page} 근거 불일치",
                )

    def test_근거가_너무_짧지_않다(self):
        for case in self.cases:
            for context in case.reference_contexts:
                self.assertGreaterEqual(len(context.evidence), 80, case.id)


if __name__ == "__main__":
    unittest.main()
