"""고객 답변에서 행동과 사기 정황을 구조화 출력으로 추출한다."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import CHAT_LLM_MAX_ATTEMPTS, CHAT_LLM_TIMEOUT_SECONDS
from app.dto.chatbot import (
    CustomerActionExtractionResult,
    FraudCircumstanceExtractionResult,
)
from app.services.chatbot.prompts import (
    render_customer_action_extraction_prompt,
    render_fraud_circumstance_extraction_prompt,
)


logger = logging.getLogger(__name__)


class ChatbotExtractionError(RuntimeError):
    """추출 LLM이 재시도 상한 안에 유효한 결과를 반환하지 못한 경우."""


class CustomerActionExtractionError(ChatbotExtractionError):
    """고객 행동 추출에 실패한 경우."""


class FraudCircumstanceExtractionError(ChatbotExtractionError):
    """사기 정황 추출에 실패한 경우."""


def _build_structured_llm(
    schema: type[BaseModel],
    *,
    model: str | None,
    timeout_seconds: float,
) -> Any:
    return ChatOpenAI(
        model=model or os.getenv("OPENAI_MODEL", "gpt-5"),
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=timeout_seconds,
        # 호출 횟수는 서비스에서 직접 관리
        max_retries=0,
    ).with_structured_output(
        schema,
        method="json_schema",
        strict=True,
    )


class CustomerActionExtractor:
    """프롬프트로 고객 행동을 추출"""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = CHAT_LLM_MAX_ATTEMPTS,
    ) -> None:
        self.structured_llm = structured_llm or _build_structured_llm(
            CustomerActionExtractionResult,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self.max_attempts = max(1, max_attempts)

    def extract(
        self,
        *,
        user_answers: str,
    ) -> CustomerActionExtractionResult:
        prompt = render_customer_action_extraction_prompt(
            user_answers=user_answers
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_result = self.structured_llm.invoke(prompt)
                result = CustomerActionExtractionResult.model_validate(
                    raw_result
                )
            except Exception as exc:
                if attempt < self.max_attempts:
                    continue
                logger.warning(
                    "고객 행동 추출 LLM 호출 실패: attempts=%s error=%s",
                    attempt,
                    type(exc).__name__,
                )
                raise CustomerActionExtractionError(
                    "customer_action 추출에 실패했습니다."
                ) from exc

            valid_actions = []
            for action in result.customer_actions:
                if action.evidence not in user_answers:
                    logger.warning(
                        "LLM 이 꾸며낸 응답이므로 패스합니다=%s"
                    )
                    continue
                valid_actions.append(action)
            return CustomerActionExtractionResult(
                customer_actions=valid_actions
            )

        raise AssertionError("고객 행동 추출 재시도 루프가 종료되었습니다.")


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
        self.structured_llm = structured_llm or _build_structured_llm(
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
    "CustomerActionExtractionError",
    "CustomerActionExtractor",
    "FraudCircumstanceExtractionError",
    "FraudCircumstanceExtractor",
]
