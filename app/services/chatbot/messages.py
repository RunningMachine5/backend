"""고객에게 그대로 출력하는 안내 문구.

문구의 출처는 docs/customer-chatbot/messages.md 한 곳뿐이다.
코드에서 문구를 새로 쓰지 않고, 바꿔야 하면 문서를 먼저 고친 뒤 여기로 옮긴다.
LLM이 생성하지 않고 애플리케이션 코드가 그대로 출력한다.
"""

from __future__ import annotations


# B.5 안내를 만들지 못한 가이드 검색 질의 — 검색 0건이거나 Generate가 답하지 못한 경우
UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE = (
    "말씀해주신 이 부분은 제가 안내해드릴 수 있는 자료를 찾지 못했어요.\n"
    "정확한 안내가 필요하시면 상담사를 연결해드릴게요."
)

# B.6(WANT_END 상담사 연결 안내)은 파이프라인이 쓰므로 B.1~B.4 와 함께 6단계에서 옮긴다.


__all__ = ["UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE"]
