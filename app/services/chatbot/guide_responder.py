"""분해된 가이드 검색 질의에 대한 RAG 대응 가이드 응답을 조립한다.

- Retrieve는 검색 질의마다 독립이며 질의 개수와 무관하게 top_k=3이다.
- Generate는 근거를 찾은 요구만 담아 LLM을 한 번 호출한다.
- 근거를 찾지 못한 요구도 소제목과 고정 안내로 응답에 나타난다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import (
    ExtractedGuideSearchQuery,
    GuideResponseGenerationResult,
    RetrievedChatbotGuideChunkDTO,
)
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.chatbot.prompts import render_guide_response_prompt
from app.services.rag.chatbot_retriever import retriever_source


logger = logging.getLogger(__name__)


RETRIEVE_TOP_K = 3
GUIDE_SEARCH_QUERY_HEADING_PREFIX = "■ "
RetrieverCallable = Callable[..., list[RetrievedChatbotGuideChunkDTO]]


@dataclass(frozen=True, slots=True)
class AugmentedGuideSearchQuery:
    """가이드 검색 질의 하나와 그 질의만을 위해 검색한 근거 청크."""

    position: int
    guide_search_query: ExtractedGuideSearchQuery
    chunks: tuple[RetrievedChatbotGuideChunkDTO, ...]

    @property
    def is_grounded(self) -> bool:
        return bool(self.chunks)


@dataclass(frozen=True, slots=True)
class GuideResponse:
    """고객 응답 본문과 근거 유무가 확인된 검색 단위 위치."""

    message_text: str
    grounded_query_positions: tuple[int, ...] = ()
    ungrounded_query_positions: tuple[int, ...] = ()


class GuideResponder:
    """가이드 검색 질의별 검색과 1회 통합 생성으로 대응 가이드를 만든다."""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        retriever: RetrieverCallable = retriever_source,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
        top_k: int = RETRIEVE_TOP_K,
    ) -> None:
        self.structured_llm = structured_llm or build_structured_llm(
            GuideResponseGenerationResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.retriever = retriever
        self.max_attempts = max(1, max_attempts)
        self.top_k = top_k

    def respond(
        self,
        *,
        guide_search_queries: Iterable[ExtractedGuideSearchQuery],
        session: Session,
    ) -> GuideResponse:
        """한 턴에서 분해된 가이드 검색 질의에 대한 안내를 만든다."""

        unique_queries = _deduplicate_guide_search_queries(guide_search_queries)
        augmented = [
            self._augment(position, query, session)
            for position, query in enumerate(unique_queries, start=1)
        ]
        grounded = [item for item in augmented if item.is_grounded]
        ungrounded = [item for item in augmented if not item.is_grounded]

        guidance_by_position = self._generate(grounded) if grounded else {}

        return GuideResponse(
            message_text=_assemble(augmented, guidance_by_position),
            grounded_query_positions=_positions(grounded),
            ungrounded_query_positions=_positions(ungrounded),
        )

    def _augment(
        self,
        position: int,
        guide_search_query: ExtractedGuideSearchQuery,
        session: Session,
    ) -> AugmentedGuideSearchQuery:
        """가이드 검색 질의 하나를 그대로 검색해 근거 청크를 붙인다."""

        try:
            chunks = self.retriever(
                guide_search_query.search_query,
                session,
                top_k=self.top_k,
            )
        except Exception as exc:
            logger.warning(
                "대응 가이드 검색 실패: position=%s error=%s",
                position,
                type(exc).__name__,
            )
            chunks = []

        return AugmentedGuideSearchQuery(
            position=position,
            guide_search_query=guide_search_query,
            chunks=tuple(chunks),
        )

    def _generate(
        self,
        grounded: Sequence[AugmentedGuideSearchQuery],
    ) -> dict[int, str]:
        """근거를 찾은 가이드 검색 질의만 담아 LLM을 한 번 호출한다."""

        prompt = render_guide_response_prompt(
            guide_search_query_context_block=_render_guide_search_query_context_block(
                grounded
            )
        )
        grounded_positions = {item.position for item in grounded}

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = GuideResponseGenerationResult.model_validate(raw_result)
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                logger.warning(
                    "대응 가이드 생성 LLM 호출 실패: attempts=%s error=%s",
                    attempt,
                    type(exc).__name__,
                )
                return {}

            guidance_by_position: dict[int, str] = {}
            for guide in result.guides:
                guidance = guide.guidance.strip()
                if not guidance:
                    continue
                if guide.position not in grounded_positions:
                    logger.warning(
                        "프롬프트에 없는 가이드 검색 질의 안내를 버립니다: position=%s",
                        guide.position,
                    )
                    continue
                guidance_by_position.setdefault(guide.position, guidance)
            return guidance_by_position

        raise AssertionError("대응 가이드 생성 재시도 루프가 종료되었습니다.")


def _deduplicate_guide_search_queries(
    guide_search_queries: Iterable[ExtractedGuideSearchQuery],
) -> list[ExtractedGuideSearchQuery]:
    """동일한 검색 질의는 처음 나온 요구만 남기고 순서를 유지한다."""

    seen: set[str] = set()
    unique_queries = []
    for query in guide_search_queries:
        normalized_query = " ".join(query.search_query.split()).casefold()
        if normalized_query in seen:
            continue
        seen.add(normalized_query)
        unique_queries.append(query)
    return unique_queries


def _render_guide_search_query_context_block(
    grounded: Sequence[AugmentedGuideSearchQuery],
) -> str:
    """대응 가이드 프롬프트의 가이드 검색 질의별 근거 블록을 조립한다."""

    blocks = []
    for item in grounded:
        query = item.guide_search_query
        lines = [
            f"[{item.position}]",
            f"소제목: {query.title}",
            f"검색 질의: {query.search_query}",
            f"고객 발언: {query.evidence}",
            "근거:",
        ]
        lines.extend(f"- {_render_chunk(chunk)}" for chunk in item.chunks)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_chunk(chunk: RetrievedChatbotGuideChunkDTO) -> str:
    """청크를 출처와 함께 한 줄로 만든다."""

    page = f" {chunk.page}p" if chunk.page is not None else ""
    return f"(출처: {chunk.source_title}{page}) {chunk.content}"


def _assemble(
    augmented: Sequence[AugmentedGuideSearchQuery],
    guidance_by_position: dict[int, str],
) -> str:
    """가이드 검색 질의 순서대로 소제목과 안내 본문을 조립한다."""

    fragments = []
    for item in augmented:
        heading = (
            f"{GUIDE_SEARCH_QUERY_HEADING_PREFIX}"
            f"{item.guide_search_query.title}"
        )
        body = (
            guidance_by_position.get(item.position)
            or UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
        )
        fragments.append(f"{heading}\n{body}")
    return "\n\n".join(fragments)


def _positions(
    items: Sequence[AugmentedGuideSearchQuery],
) -> tuple[int, ...]:
    return tuple(item.position for item in items)


__all__ = [
    "AugmentedGuideSearchQuery",
    "GuideResponder",
    "GuideResponse",
    "GUIDE_SEARCH_QUERY_HEADING_PREFIX",
    "RETRIEVE_TOP_K",
]
