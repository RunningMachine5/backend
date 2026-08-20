"""구조화 출력 LLM 클라이언트 생성기의 지연 예산 설정을 고정한다.

reasoning effort 를 넘기지 않거나 타임아웃을 가장 느린 호출보다 짧게 잡으면
정상 응답이 매번 타임아웃으로 버려져 모든 턴이 기술 실패 경로로 빠진다
(PRD 2.4 「reasoning effort와 타임아웃 예산」). 실제로 그 회귀가 있었으므로
env var 기본값과 인자 전달을 테스트로 묶어 둔다.
"""

import unittest
from unittest import mock

from app.core.config import (
    CHAT_LLM_REASONING_EFFORT,
    CHAT_LLM_TIMEOUT_SECONDS,
)
from app.dto.chatbot import AnswerEvaluationResult
from app.services.chatbot import llm as llm_module


class TestBuildStructuredLLM(unittest.TestCase):
    def _build(self, **kwargs: object) -> mock.MagicMock:
        """ChatOpenAI 를 대체해 생성자에 넘어간 인자만 확인한다."""

        with mock.patch.object(llm_module, "ChatOpenAI") as chat_openai:
            llm_module.build_structured_llm(
                AnswerEvaluationResult,
                timeout_seconds=CHAT_LLM_TIMEOUT_SECONDS,
                **kwargs,
            )
        return chat_openai

    def test_passes_reasoning_effort_by_default(self) -> None:
        chat_openai = self._build()

        self.assertEqual(
            chat_openai.call_args.kwargs["reasoning_effort"],
            CHAT_LLM_REASONING_EFFORT,
        )

    def test_omits_reasoning_effort_when_blank(self) -> None:
        """추론 모델이 아닌 모델로 바꿔 끼울 수 있어야 한다."""

        chat_openai = self._build(reasoning_effort="")

        self.assertNotIn("reasoning_effort", chat_openai.call_args.kwargs)

    def test_forwards_timeout_and_disables_client_retries(self) -> None:
        chat_openai = self._build()

        self.assertEqual(
            chat_openai.call_args.kwargs["timeout"],
            CHAT_LLM_TIMEOUT_SECONDS,
        )
        # 재시도 횟수는 각 서비스가 직접 관리한다.
        self.assertEqual(chat_openai.call_args.kwargs["max_retries"], 0)

    def test_plain_text_builder_does_not_wrap_structured_output(self) -> None:
        with mock.patch.object(llm_module, "ChatOpenAI") as chat_openai:
            result = llm_module.build_chat_llm(
                timeout_seconds=CHAT_LLM_TIMEOUT_SECONDS,
            )

        self.assertIs(result, chat_openai.return_value)
        chat_openai.return_value.with_structured_output.assert_not_called()


class TestChatLLMLatencyBudget(unittest.TestCase):
    def test_default_reasoning_effort_is_low(self) -> None:
        """minimal 은 충실한 답변을 TOO_VAGUE 로 오판해 쓰지 않는다."""

        self.assertEqual(CHAT_LLM_REASONING_EFFORT, "low")

    def test_default_timeout_covers_slowest_call(self) -> None:
        """가장 느린 가이드 검색 질의 분해(A.2)가 약 9.5초다."""

        self.assertGreaterEqual(CHAT_LLM_TIMEOUT_SECONDS, 15)


if __name__ == "__main__":
    unittest.main()
