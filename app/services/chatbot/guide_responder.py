"""추출된 고객 행동에 대한 RAG 대응 가이드 응답을 조립

- Retrieve 는 액션당 독립이며 top_k 는 액션 개수와 무관하게 3 고정
- Generate 는 근거를 찾은 액션(grounded)만 담아 LLM 을 딱 한 번 호출한다
- 근거를 찾지 못한 액션도 소제목과 함께 응답 목록에 나타난다(B.5)

프롬프트: [A.4](docs/customer-chatbot/prompts.md)
문구: [B.5](docs/customer-chatbot/messages.md)가 출처다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.domain.customer_action_codes import (
    CUSTOMER_ACTION_DESCRIPTIONS,
    CUSTOMER_ACTION_SEARCH_QUERIES,
)
from app.dto.chatbot import (
    ExtractedCustomerAction,
    GuideResponseGenerationResult,
    RetrievedChatbotGuideChunkDTO,
)
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.messages import UNGROUNDED_ACTION_MESSAGE
from app.services.chatbot.prompts import render_guide_response_prompt
from app.services.rag.chatbot_retriever import retriever_source


logger = logging.getLogger(__name__)


# 액션 개수와 무관한 고정값 (PRD 2.5)
RETRIEVE_TOP_K = 3

# 응답 목록에서 액션 소제목 앞에 붙이는 기호
ACTION_HEADING_PREFIX = "■ "

#Callable[..., xxx]  # 입력은 ALL 출력은 xxx 로 강제하는 느낌
RetrieverCallable = Callable[..., list[RetrievedChatbotGuideChunkDTO]]


@dataclass(frozen=True, slots=True)
class AugmentedAction:
    """액션 하나와 그 액션만을 위해 검색한 근거 청크."""

    action: ExtractedCustomerAction
    chunks: tuple[RetrievedChatbotGuideChunkDTO, ...]

    # 청크가 있는지 없는지 확인하는 메소드, getter 메소드랑 비슷하게 동작
    @property
    def is_chunk(self) -> bool:
        return bool(self.chunks)


@dataclass(frozen=True, slots=True)
class GuideResponse:
    """고객에게 보낼 응답 본문과 어떤 액션이 근거를 얻었는지에 대한 기록.

    추출된 액션이 하나도 없으면 `message_text` 는 빈 문자열이다. 이때 파이프라인은
    대응 가이드 메시지를 보내지 않고 다음 질문으로 넘어간다.
    """

    message_text: str
    grounded_action_codes: tuple[str, ...] = () # 고객행동에 대한 관련 청크를 찾은 고객행동들
    ungrounded_action_codes: tuple[str, ...] = () # 관련 청크를 찾지 못한것들


class GuideResponder:
    """액션별 검색과 1회 통합 생성으로 대응 가이드 응답을 만든다."""
    # RAG 핵심 로직
    # https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680833737659&cot=14

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
        # 어떤 리트리버 사용할건지 등등 추가정보 추가
        self.retriever = retriever
        self.max_attempts = max(1, max_attempts)
        self.top_k = top_k

    def respond(
        self,
        *,
        actions: Iterable[ExtractedCustomerAction],
        session: Session,
    ) -> GuideResponse:
        """핵심 오케이스레이션 로직, SUFFICIENT 판정 한 턴에 대해 대응 가이드 응답을 만든다."""

        """R-A 단계"""
        # 각 고객 행동들에 대해 대응가이드 청크를 가져와서 붙이는 로직 R-A 단게
        augmented = [
            self._augment(action, session)
            for action in _deduplicate_actions(actions)
        ]
        grounded = [item for item in augmented if item.is_chunk] # 대응가이드를 찾은 고객행동들
        ungrounded = [item for item in augmented if not item.is_chunk] # 대응가이드를 찾지 못한 고객행동들

        """G 단계"""
        # 근거가 하나도 없으면 굳이 LLM 을 부르지 앟는다
        # 모든 액션이 대체 문구로 채워진다
        guidance_by_action = self._generate(grounded) if grounded else {}

        return GuideResponse(
            # ~에 대한 대응가이드 형식으로 조립니다
            message_text=_assemble(augmented, guidance_by_action),
            grounded_action_codes=_codes(grounded),
            ungrounded_action_codes=_codes(ungrounded),
        )

    def _augment(
        self,
        action: ExtractedCustomerAction,
        session: Session,
    ) -> AugmentedAction:
        """액션 하나를 독립적으로 검색해 근거 청크를 붙인다."""

        # enum 영어 원문을 그대로 임베딩하면 한국어 가이드랑 유사도가 잡히지 않으므로 변환
        search_query = CUSTOMER_ACTION_SEARCH_QUERIES.get(action.type)

        # 검색 결과가 없는 경우
        if search_query is None:
            logger.warning(
                "검색 질의 매핑이 없는 고객 행동이라 근거 없이 처리합니다: type=%s",
                action.type,
            )
            return AugmentedAction(action=action, chunks=())

        query = f"{search_query} {action.evidence}"
        try:
            chunks = self.retriever(query, session, top_k=self.top_k)
        # 뭔가 검색하다가 찐빠난 경우
        except Exception as exc:
            # 검색 실패는 해당 액션만 0건으로 떨어뜨리고 상담을 계속한다.
            logger.warning(
                "대응 가이드 검색 실패: type=%s error=%s",
                action.type,
                type(exc).__name__,
            )
            return AugmentedAction(action=action, chunks=())

        #정상 결과 출력
        return AugmentedAction(action=action, chunks=tuple(chunks))

    def _generate(
        self,
        grounded: Sequence[AugmentedAction],
    ) -> dict[str, str]:
        """근거를 찾은 액션만 담아 LLM 을 한 번 호출한다."""

        prompt = render_guide_response_prompt(
            action_context_block=_render_action_context_block(grounded)
        )
        grounded_codes = {item.action.type for item in grounded}

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = GuideResponseGenerationResult.model_validate(
                    raw_result
                )
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                logger.warning(
                    "대응 가이드 생성 LLM 호출 실패: attempts=%s error=%s",
                    attempt,
                    type(exc).__name__,
                )
                # 안내가 하나도 없으면 조립 단계가 전부 B.5 문구로 채운다.
                return {}

            guidance_by_action: dict[str, str] = {}
            for guide in result.guides:
                guidance = guide.guidance.strip()
                if not guidance:
                    continue
                if guide.type not in grounded_codes:
                    # 프롬프트에 넣지 않은 액션까지 답한 경우는 근거가 없다.
                    logger.warning(
                        "프롬프트에 없는 고객 행동의 안내라 버립니다: type=%s",
                        guide.type,
                    )
                    continue
                guidance_by_action.setdefault(guide.type, guidance)
            return guidance_by_action

        raise AssertionError("대응 가이드 생성 재시도 루프가 종료되었습니다.")

def _deduplicate_actions(
    actions: Iterable[ExtractedCustomerAction],
) -> list[ExtractedCustomerAction]:
    """같은 행동이 두 번 오면 먼저 나온 것만 남긴다(고객이 말한 순서 유지)."""

    seen: set[str] = set()
    unique_actions = []
    for action in actions:
        if action.type in seen:
            continue
        seen.add(action.type)
        unique_actions.append(action)
    return unique_actions


def _render_action_context_block(
    grounded: Sequence[AugmentedAction],
) -> str:
    """A.4 프롬프트의 액션별 근거 블록을 조립한다."""

    blocks = []
    for index, item in enumerate(grounded, start=1):
        action = item.action
        lines = [
            f"[{index}] {action.type}",
            f"행동 설명: {CUSTOMER_ACTION_DESCRIPTIONS[action.type]}",
            f"고객 발언: {action.evidence}",
            "근거:",
        ]
        lines.extend(f"- {_render_chunk(chunk)}" for chunk in item.chunks)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_chunk(chunk: RetrievedChatbotGuideChunkDTO) -> str:
    """청크를 출처와 함께 한 줄로 만든다."""

    page = f" {chunk.page}p" if chunk.page is not None else ""
    return f"(출처: {chunk.source_title}{page}) {chunk.content}"

# 어밴져스!
def _assemble(
    augmented: Sequence[AugmentedAction],
    guidance_by_action: dict[str, str],
) -> str:
    """
    augmented 와 guidance_by_action 를 받아서 고객 응답으로 가공하는 함수
    """

    fragments = []
    for item in augmented:
        action_type = item.action.type
        heading = (
            f"{ACTION_HEADING_PREFIX}"
            f"{CUSTOMER_ACTION_DESCRIPTIONS[action_type]}"
        )
        # 대응가이드 있으면 그거 넣고 아니면 기본메시지 넣는다
        body = guidance_by_action.get(action_type) or UNGROUNDED_ACTION_MESSAGE
        fragments.append(f"{heading}\n{body}")
    return "\n\n".join(fragments)

# 코드만 따로 빼주는 헬퍼 함수
def _codes(items: Sequence[AugmentedAction]) -> tuple[str, ...]:
    return tuple(item.action.type for item in items)


__all__ = [
    "ACTION_HEADING_PREFIX",
    "AugmentedAction",
    "GuideResponder",
    "GuideResponse",
    "RETRIEVE_TOP_K",
]
