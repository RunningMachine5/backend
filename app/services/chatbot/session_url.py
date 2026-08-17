"""고객이 접속할 챗봇 세션 URL을 만든다."""

from app.core.config import CHAT_BASE_URL


def build_chat_url(chat_session_id: str) -> str:
    """``CHAT_BASE_URL``에 세션별 고객 화면 경로를 붙인다."""

    return f"{CHAT_BASE_URL}/chat/{chat_session_id}"


__all__ = ["build_chat_url"]
