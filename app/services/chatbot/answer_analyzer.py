"""고객 답변 판정과 가이드 검색 질의 분해를 한 구조화 출력으로 수행한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import (
    AnswerAnalysisResult,
    AnswerQualityVerdict,
    ExtractedGuideSearchQuery,
)
from app.services.chatbot.extractors import normalize_guide_search_queries
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.prompts import render_answer_analysis_prompt


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnswerAnalysisOutcome:
    """통합 분석 결과와 기존 저장 계약에서 사용할 실패 메타데이터."""

    quality_verdict: AnswerQualityVerdict | None
    guide_search_queries: tuple[ExtractedGuideSearchQuery, ...] = ()
    verdict_skip_reason: Literal["EVALUATOR_FAILED"] | None = None


class AnswerAnalyzer:
    """답변 판정과 검색 질의 분해를 단일 LLM 호출로 수행한다."""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
    ) -> None:
        self.structured_llm = structured_llm or build_structured_llm(
            AnswerAnalysisResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.max_attempts = max(1, max_attempts)

    def analyze(
        self,
        *,
        question_text: str,
        customer_answer: str,
    ) -> AnswerAnalysisOutcome:
        """한 호출의 구조화 출력으로 판정과 검색 질의를 반환한다."""

        prompt = render_answer_analysis_prompt(
            question_text=question_text,
            customer_answer=customer_answer,
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = AnswerAnalysisResult.model_validate(raw_result)
            except Exception as exc:
                logger.warning(
                    "챗봇 답변 통합 분석 LLM 호출 실패: attempt=%s/%s error=%s: %s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self.max_attempts:
                    continue
                return AnswerAnalysisOutcome(
                    quality_verdict=None,
                    verdict_skip_reason="EVALUATOR_FAILED",
                )

            if result.verdict is not AnswerQualityVerdict.SUFFICIENT:
                if result.guide_search_queries:
                    logger.debug(
                        "비충실 답변의 가이드 검색 질의를 폐기합니다: verdict=%s count=%s",
                        result.verdict,
                        len(result.guide_search_queries),
                    )
                return AnswerAnalysisOutcome(quality_verdict=result.verdict)

            normalized = normalize_guide_search_queries(
                result.guide_search_queries,
                user_answers=customer_answer,
            )
            return AnswerAnalysisOutcome(
                quality_verdict=result.verdict,
                guide_search_queries=tuple(normalized),
            )

        raise AssertionError("답변 통합 분석 재시도 루프가 결과 없이 종료되었습니다.")


__all__ = ["AnswerAnalysisOutcome", "AnswerAnalyzer"]
