import unittest
from unittest.mock import patch

from app.services.chatbot.session_url import build_chat_url


class ChatSessionUrlTest(unittest.TestCase):
    @patch("app.services.chatbot.session_url.CHAT_BASE_URL", "https://example.test")
    def test_builds_session_specific_url(self) -> None:
        self.assertEqual(
            build_chat_url("CHAT-001"),
            "https://example.test/chat/CHAT-001",
        )


if __name__ == "__main__":
    unittest.main()
