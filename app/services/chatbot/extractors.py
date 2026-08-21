"""고객 답변에서 가이드 검색 질의와 사기 정황을 구조화 출력으로 추출한다."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import (
    ExtractedGuideSearchQuery,
    FraudCircumstanceExtractionResult,
)
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.prompts import (
    render_fraud_circumstance_extraction_prompt,
)


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


class ChatbotExtractionError(RuntimeError):
    """추출 LLM이 재시도 상한 안에 유효한 결과를 반환하지 못한 경우."""


class FraudCircumstanceExtractionError(ChatbotExtractionError):
    """사기 정황 추출에 실패한 경우."""


class FraudCircumstanceExtractor:
    """프롬프트로 사기 정황을 추출"""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
    ) -> None:
        self.structured_llm = structured_llm or build_structured_llm(
            FraudCircumstanceExtractionResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.max_attempts = max(1, max_attempts)

    def extract(
        self,
        *,
        user_answers: str,
    ) -> FraudCircumstanceExtractionResult:
        prompt = render_fraud_circumstance_extraction_prompt(
            user_answers=user_answers
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = FraudCircumstanceExtractionResult.model_validate(
                    raw_result
                )
            except Exception as exc:
                logger.warning(
                    "사기 정황 추출 LLM 호출 실패: attempt=%s/%s error=%s: %s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self.max_attempts:
                    continue
                raise FraudCircumstanceExtractionError(
                    "fraud_circumstance 추출에 실패했습니다."
                ) from exc # 예외 체이닝

            valid_circumstances = []
            for circumstance in result.fraud_circumstances:
                if circumstance.evidence not in user_answers:
                    logger.warning(
                        "LLM 이 꾸며낸 응답이므로 패스합니다: evidence=%s",
                        circumstance.evidence,
                    )
                    continue
                valid_circumstances.append(circumstance)
            return FraudCircumstanceExtractionResult(
                fraud_circumstances=valid_circumstances
            )

        raise AssertionError("사기 정황 추출 재시도 루프가 종료되었습니다.")


__all__ = [
    "ChatbotExtractionError",
    "FraudCircumstanceExtractionError",
    "FraudCircumstanceExtractor",
    "normalize_guide_search_queries",
]


def _normalize_search_query(search_query: str) -> str:
    """중복 비교를 위해 검색 질의의 공백과 대소문자를 정규화한다."""

    return " ".join(search_query.split()).casefold()
