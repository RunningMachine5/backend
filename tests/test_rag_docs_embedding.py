"""PDF 페이지 텍스트 정규화 검증.

NUL(0x00)이 섞인 PDF 는 PostgreSQL text 컬럼에 저장되지 않아 적재가 통째로
실패한다(psycopg DataError). 실제로 C11.pdf, P02.pdf 가 이 때문에 적재되지
않고 있었다. 정규화 단계에서 걸러내는 것을 회귀로 고정한다.
"""

import unittest

from app.services.rag.docs_embedding import normalize_page_text


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


if __name__ == "__main__":
    unittest.main()
