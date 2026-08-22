import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app.scripts.download_latest_fss_pdfs import download_pdfs, fetch_documents


class DownloadLatestFssPdfsTest(unittest.TestCase):
    @patch("app.scripts.download_latest_fss_pdfs.requests.post")
    def test_fetch_documents_parses_fss_response(self, post):
        post.return_value = Mock(
            json=lambda: {
                "reponse": {"resultCode": "1", "result": [{"contentId": "1"}]}
            }
        )

        result = fetch_documents("x" * 32, date(2026, 8, 20), date(2026, 8, 21))

        self.assertEqual(result, [{"contentId": "1"}])
        post.return_value.raise_for_status.assert_called_once()

    @patch("app.scripts.download_latest_fss_pdfs.requests.get")
    def test_download_pdfs_saves_only_new_pdf(self, get):
        get.return_value = Mock(content=b"%PDF-1.7\ncontent")
        documents = [
            {
                "atchfileNm": "notice.hwp|%EB%B3%B4%EB%8F%84%EC%9E%90%EB%A3%8C.pdf",
                "atchfileUrl": "https://example.com/1|https://example.com/2&amp;x=1",
            }
        ]

        with tempfile.TemporaryDirectory() as directory:
            saved = download_pdfs(documents, Path(directory))

            self.assertEqual([path.name for path in saved], ["보도자료.pdf"])
            self.assertEqual(saved[0].read_bytes(), b"%PDF-1.7\ncontent")
            get.assert_called_once_with("https://example.com/2&x=1", timeout=60)


if __name__ == "__main__":
    unittest.main()
