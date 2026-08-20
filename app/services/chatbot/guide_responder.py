"""분해된 가이드 검색 질의에 대한 RAG 대응 가이드 응답을 생성한다.

- Retrieve는 검색 질의마다 독립이며 질의 개수와 무관하게 top_k=3이다.
- Generate는 검색 질의 전체를 담아 일반 텍스트 LLM을 한 번 호출한다. 근거가 없는
  위치의 B.5 문구와 소제목까지 최종 고객 본문으로 생성한다. 고객에게 그대로 나가는
  유일한 생성이므로 여기만 CHAT_RESPONSE_LLM_MODEL 을 쓴다. 지금 이 값은
  CHAT_LLM_MODEL 과 같고, 이 생성만 따로 갈아끼울 여지를 두려고 변수를 나눠 뒀다.
- 모든 위치가 무근거거나 생성이 끝내 실패하면 애플리케이션이 B.5 본문을 조립한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.core.config import (
    CHAT_LLM_MAX_ATTEMPTS,
    CHAT_LLM_TIMEOUT_SECONDS,
    CHAT_RESPONSE_LLM_MODEL,
)
from app.dto.chatbot import ExtractedGuideSearchQuery, RetrievedChatbotGuideChunkDTO
from app.services.chatbot.llm import build_chat_llm
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE
from app.services.chatbot.prompts import render_guide_response_prompt
from app.services.rag.chatbot_retriever import retriever_source


logger = logging.getLogger(__name__)


RETRIEVE_TOP_K = 3
GUIDE_SEARCH_QUERY_HEADING_PREFIX = "■ "
RetrieverCallable = Callable[..., list[RetrievedChatbotGuideChunkDTO]]
SnapshotCallback = Callable[[str], None]


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
        llm: Any | None = None,
        retriever: RetrieverCallable = retriever_source,
        model: str = CHAT_RESPONSE_LLM_MODEL,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
        top_k: int = RETRIEVE_TOP_K,
    ) -> None:
        self.llm = llm or build_chat_llm(
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
        on_snapshot: SnapshotCallback | None = None,
    ) -> GuideResponse:
        """한 턴의 최종 안내를 만들고 생성 중에는 누적 본문을 알린다."""

        unique_queries = _deduplicate_guide_search_queries(guide_search_queries)
        augmented = [
            self._augment(position, query, session)
            for position, query in enumerate(unique_queries, start=1)
        ]
        grounded = [item for item in augmented if item.is_grounded]
        ungrounded = [item for item in augmented if not item.is_grounded]

        if not augmented:
            message_text = ""
        elif not grounded:
            # 근거가 하나도 없으면 모델이 새 안내를 만들 여지가 없으므로 호출하지 않는다.
            message_text = _assemble_fallback(augmented)
        else:
            message_text = self._generate(augmented, on_snapshot=on_snapshot)

        return GuideResponse(
            message_text=message_text,
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
        augmented: Sequence[AugmentedGuideSearchQuery],
        *,
        on_snapshot: SnapshotCallback | None,
    ) -> str:
        """질의 전체를 일반 텍스트 한 번으로 생성하고 누적 본문을 전달한다."""

        prompt = render_guide_response_prompt(
            guide_search_query_context_block=_render_guide_search_query_context_block(
                augmented
            ),
            ungrounded_message=UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE,
        )

        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1 and on_snapshot is not None:
                # 이전 시도의 일부 문장이 화면에 남지 않도록 누적 본문을 초기화한다.
                on_snapshot("")

            try:
                message_text = ""
                for chunk in self.llm.stream(prompt):
                    delta = _chunk_text(chunk)
                    if not delta:
                        continue
                    message_text += delta
                    if on_snapshot is not None:
                        on_snapshot(message_text)

                streamed_message_text = message_text
                message_text = streamed_message_text.strip()
                if not message_text:
                    raise ValueError("대응 가이드 생성 결과가 비어 있습니다")
            except Exception as exc:
                logger.warning(
                    "대응 가이드 생성 LLM 호출 실패: attempt=%s/%s error=%s: %s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self.max_attempts:
                    continue
                fallback = _assemble_fallback(augmented)
                if on_snapshot is not None:
                    on_snapshot(fallback)
                return fallback

            if (
                on_snapshot is not None
                and message_text != streamed_message_text
            ):
                # 앞뒤 공백을 제거한 저장 본문과 화면의 마지막 스냅샷을 맞춘다.
                on_snapshot(message_text)
            return message_text

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
    augmented: Sequence[AugmentedGuideSearchQuery],
) -> str:
    """대응 가이드 프롬프트의 가이드 검색 질의별 근거 블록을 조립한다."""

    blocks = []
    for item in augmented:
        query = item.guide_search_query
        lines = [
            f"[{item.position}]",
            f"소제목: {query.title}",
            f"검색 질의: {query.search_query}",
            f"고객 발언: {query.evidence}",
            "근거:",
        ]
        if item.chunks:
            lines.extend(f"- {_render_chunk(chunk)}" for chunk in item.chunks)
        else:
            lines.append("없음")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_chunk(chunk: RetrievedChatbotGuideChunkDTO) -> str:
    """청크를 출처와 함께 한 줄로 만든다."""

    page = f" {chunk.page}p" if chunk.page is not None else ""
    return f"(출처: {chunk.source_title}{page}) {chunk.content}"


def _assemble_fallback(
    augmented: Sequence[AugmentedGuideSearchQuery],
) -> str:
    """생성을 못 하는 경우 모든 위치를 B.5 고정 문구로 조립한다."""

    fragments = []
    for item in augmented:
        heading = (
            f"{GUIDE_SEARCH_QUERY_HEADING_PREFIX}"
            f"{item.guide_search_query.title}"
        )
        fragments.append(f"{heading}\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}")
    return "\n\n".join(fragments)


def _chunk_text(chunk: Any) -> str:
    """LangChain 메시지 청크에서 고객에게 보여줄 일반 텍스트만 꺼낸다."""

    if isinstance(chunk, str):
        return chunk

    text = getattr(chunk, "text", None)
    if isinstance(text, str):
        return text

    content = getattr(chunk, "content", None)
    return content if isinstance(content, str) else ""


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
