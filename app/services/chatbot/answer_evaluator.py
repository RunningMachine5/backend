"""고객 답변의 충실도를 구조화 출력으로 평가한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import AnswerEvaluationResult, AnswerQualityVerdict
from app.services.chatbot.llm import build_structured_llm
from app.services.chatbot.prompts import render_quality_check_prompt


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnswerEvaluationOutcome:
    """평가 결과와 파이프라인·저장소에서 사용할 실패 메타데이터."""

    routing_verdict: AnswerQualityVerdict
    quality_verdict: AnswerQualityVerdict | None
    verdict_skip_reason: Literal["EVALUATOR_FAILED"] | None = None


class AnswerEvaluator:
    """프롬프트로 고객 답변을 평가"""
    # routing_verdict: 파이브라인이 다음에 어떻게 움직일지 결정
    # quality_verdict: LLM이 실제로 판정한 결과를 DB에 기록
    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
    ) -> None:
        self.structured_llm = structured_llm or build_structured_llm(
            AnswerEvaluationResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.max_attempts = max(1, max_attempts)

    def evaluate(
        self,
        *,
        question_text: str,
        customer_answer: str,
    ) -> AnswerEvaluationOutcome:
        """고객 답변을 평가합니다"""
        
        prompt = render_quality_check_prompt(
            question_text=question_text,
            customer_answer=customer_answer,
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = AnswerEvaluationResult.model_validate(raw_result)
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                logger.warning(
                    "챗봇 답변 평가 LLM 호출 실패: attempts=%s error=%s",
                    attempt,
                    type(exc).__name__,
                )
                #평가 실패 시 DB엔 quality_verdict 를 저장하지 말고 verdict_skip_reason을 저장한다
                return AnswerEvaluationOutcome(
                    routing_verdict=AnswerQualityVerdict.REFUSAL,
                    quality_verdict=None,
                    verdict_skip_reason="EVALUATOR_FAILED",
                )

            return AnswerEvaluationOutcome(
                routing_verdict=result.verdict,
                quality_verdict=result.verdict,
            )

        raise AssertionError("답변 평가 재시도 루프가 결과 없이 종료되었습니다.")


__all__ = ["AnswerEvaluationOutcome", "AnswerEvaluator"]
