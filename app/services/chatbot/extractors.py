"""고객 답변에서 가이드 검색 질의와 사기 정황을 구조화 출력으로 추출한다."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import (
    FraudCircumstanceExtractionResult,
    GuideSearchQueryExtractionResult,
)
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.prompts import (
    render_fraud_circumstance_extraction_prompt,
    render_guide_search_query_extraction_prompt,
)


logger = logging.getLogger(__name__)


class ChatbotExtractionError(RuntimeError):
    """추출 LLM이 재시도 상한 안에 유효한 결과를 반환하지 못한 경우."""


class GuideSearchQueryExtractionError(ChatbotExtractionError):
    """가이드 검색 질의 분해에 실패한 경우."""


class FraudCircumstanceExtractionError(ChatbotExtractionError):
    """사기 정황 추출에 실패한 경우."""


class GuideSearchQueryExtractor:
    """고객 답변을 독립적인 가이드 검색 질의로 분해한다."""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
    ) -> None:
        self.structured_llm = structured_llm or build_structured_llm(
            GuideSearchQueryExtractionResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.max_attempts = max(1, max_attempts)

    def extract(
        self,
        *,
        user_answers: str,
    ) -> GuideSearchQueryExtractionResult:
        # 분해 전용 프롬프트 요청
        prompt = render_guide_search_query_extraction_prompt(
            user_answers=user_answers
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = GuideSearchQueryExtractionResult.model_validate(
                    raw_result
                )
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                logger.warning(
                    "가이드 검색 질의 분해 LLM 호출 실패: attempts=%s error=%s",
                    attempt,
                    type(exc).__name__,
                )
                raise GuideSearchQueryExtractionError(
                    "guide_search_query 분해에 실패했습니다."
                ) from exc

            valid_queries = []
            seen_queries: set[str] = set()
            for query in result.guide_search_queries:
                # 이상한 증거를 가져왔을때 거를려고
                if query.evidence not in user_answers:
                    logger.warning(
                        "원문에 없는 가이드 검색 질의 evidence를 버립니다: evidence=%s",
                        query.evidence,
                    )
                    continue

                title = query.title.strip()
                search_query = " ".join(query.search_query.split())
                if not title or not search_query:
                    continue

                #중복 검사 부분
                normalized_query = _normalize_search_query(search_query)
                # 만약 이미 쿼리 리스트에 있었다면 패스
                if normalized_query in seen_queries:
                    continue
                # 쿼리 리스트에 저장 (seen_queries는 지금까지 쿼리 정보를 모아둔 사전 역할을 한다)
                seen_queries.add(normalized_query)
                valid_queries.append(
                    # 기존 쿼리에서 evidence는 유지하고 title,search_query 만 정규화된 값으로 교체
                    query.model_copy(
                        update={
                            "title": title,
                            "search_query": search_query,
                        }
                    )
                )
            return GuideSearchQueryExtractionResult(
                guide_search_queries=valid_queries
            )

        raise AssertionError("가이드 검색 질의 분해 재시도 루프가 종료되었습니다.")


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
                # list[ExtractedFraudCircumstance] 형식이 맞는지 검사
                # LLM 에서 이상한게 들어왔을 수도 있기 때문
                result = FraudCircumstanceExtractionResult.model_validate(
                    raw_result
                )
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                raise FraudCircumstanceExtractionError(
                    "fraud_circumstance 추출에 실패했습니다."
                ) from exc # 예외 체이닝

            valid_circumstances = []
            for circumstance in result.fraud_circumstances:
                if circumstance.evidence not in user_answers:
                    logger.warning(
                        "LLM 이 꾸며낸 응답이므로 패스합니다=%s"
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
    "GuideSearchQueryExtractionError",
    "GuideSearchQueryExtractor",
]


def _normalize_search_query(search_query: str) -> str:
    """중복 비교를 위해 검색 질의의 공백과 대소문자를 정규화한다."""

    return " ".join(search_query.split()).casefold()
