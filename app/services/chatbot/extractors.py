"""고객 답변에서 만든 가이드 검색 질의를 정규화한다."""

from __future__ import annotations

import logging
from app.dto.chatbot import ExtractedGuideSearchQuery


logger = logging.getLogger(__name__)


def normalize_guide_search_queries(
    queries: list[ExtractedGuideSearchQuery],
    *,
    user_answers: str,
) -> list[ExtractedGuideSearchQuery]:
    """검색 질의의 공백을 정리하고 같은 질의를 한 번만 유지한다."""

    valid_queries = []
    seen_queries: set[str] = set()
    for query in queries:
        # evidence는 검색보다 감사 기록에 쓰이므로 원문과 달라도 질의를 버리지 않는다.
        if query.evidence not in user_answers:
            logger.debug(
                "가이드 검색 질의 evidence가 답변 원문과 다릅니다: evidence=%s",
                query.evidence,
            )

        title = query.title.strip()
        search_query = " ".join(query.search_query.split())
        if not title or not search_query:
            continue

        normalized_query = _normalize_search_query(search_query)
        if normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        valid_queries.append(
            query.model_copy(
                update={
                    "title": title,
                    "search_query": search_query,
                }
            )
        )
    return valid_queries


__all__ = ["normalize_guide_search_queries"]


def _normalize_search_query(search_query: str) -> str:
    """중복 비교를 위해 검색 질의의 공백과 대소문자를 정규화한다."""

    return " ".join(search_query.split()).casefold()
