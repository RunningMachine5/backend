import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import sleep

from app.domain.agent_status import InformationStatus
from app.domain.response_policy import (
    PolicyAction,
    PolicyChecklistItem,
    ResponsePolicy,
)
from app.dto.agent_guide import RetrievedGuideChunkDTO
from app.services.agent.response_plan_generator import (
    GeneratedActionDetail,
    GeneratedResponsePlan,
    RagResponsePlanGenerator,
)


class FakeStructuredLLM:
    def __init__(
        self,
        result=None,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.messages = None

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.delay_seconds:
            sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return self.result


class RagResponsePlanGeneratorTest(unittest.TestCase):
    def test_rag_context_enriches_policy_actions(self) -> None:
        llm = FakeStructuredLLM(
            GeneratedResponsePlan(
                actions=[
                    GeneratedActionDetail(
                        action_code="VERIFY_CUSTOMER_TRANSACTION",
                        procedure_steps=["등록 연락처로 본인 거래 여부를 확인한다."],
                        cautions=["비밀번호와 인증번호를 요청하지 않는다."],
                    )
                ],
            )
        )
        generator = RagResponsePlanGenerator(structured_llm=llm)

        result = generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
        )

        self.assertEqual(result.information_status, InformationStatus.SUFFICIENT)
        self.assertEqual(result.recommended_actions[0].priority, 1)
        self.assertEqual(
            result.recommended_actions[0].procedure_steps,
            ["등록 연락처로 본인 거래 여부를 확인한다."],
        )
        self.assertIn("대응 절차", llm.messages[1]["content"])

    def test_unknown_action_code_uses_policy_fallback(self) -> None:
        llm = FakeStructuredLLM(
            GeneratedResponsePlan(
                actions=[
                    GeneratedActionDetail(
                        action_code="UNKNOWN_ACTION",
                        procedure_steps=["임의 조치"],
                        cautions=[],
                    )
                ],
            )
        )

        result = RagResponsePlanGenerator(structured_llm=llm).generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
        )

        self.assertEqual(
            result.recommended_actions[0].action_code,
            "VERIFY_CUSTOMER_TRANSACTION",
        )
        self.assertEqual(result.recommended_actions[0].procedure_steps, [])

    def test_llm_failure_uses_policy_fallback(self) -> None:
        generator = RagResponsePlanGenerator(
            structured_llm=FakeStructuredLLM(error=RuntimeError("LLM 실패"))
        )

        result = generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
        )

        self.assertEqual(result.applied_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(result.recommended_actions[0].procedure_steps, [])

    def test_empty_guides_skip_llm_and_return_partial_plan(self) -> None:
        llm = FakeStructuredLLM()
        result = RagResponsePlanGenerator(structured_llm=llm).generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[],
        )

        self.assertEqual(llm.calls, 0)
        self.assertEqual(result.information_status, InformationStatus.PARTIAL)

    def test_same_policy_and_guides_use_cached_plan(self) -> None:
        llm = FakeStructuredLLM(self._generated_plan())
        generator = RagResponsePlanGenerator(structured_llm=llm)
        first_metrics = {}
        second_metrics = {}

        first = generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
            metrics=first_metrics,
        )
        second = generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
            metrics=second_metrics,
        )

        self.assertEqual(first, second)
        self.assertEqual(llm.calls, 1)
        self.assertFalse(first_metrics["response_plan_cache_hit"])
        self.assertEqual(first_metrics["response_plan_llm_call_count"], 1)
        self.assertTrue(second_metrics["response_plan_cache_hit"])
        self.assertEqual(second_metrics["response_plan_llm_call_count"], 0)

    def test_cache_size_zero_calls_llm_for_each_evaluation_run(self) -> None:
        llm = FakeStructuredLLM(self._generated_plan())
        generator = RagResponsePlanGenerator(structured_llm=llm, cache_size=0)

        for _ in range(2):
            generator.generate(
                fraud_type="ACCOUNT_TAKEOVER",
                policy=self._policy(),
                guides=[self._guide()],
            )

        self.assertEqual(llm.calls, 2)

    def test_guide_content_change_invalidates_cache(self) -> None:
        llm = FakeStructuredLLM(self._generated_plan())
        generator = RagResponsePlanGenerator(structured_llm=llm)

        generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[self._guide()],
        )
        generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=self._policy(),
            guides=[replace(self._guide(), content="변경된 고객 확인 절차")],
        )

        self.assertEqual(llm.calls, 2)

    def test_policy_change_invalidates_cache(self) -> None:
        llm = FakeStructuredLLM(self._generated_plan())
        generator = RagResponsePlanGenerator(structured_llm=llm)
        policy = self._policy()
        changed_policy = replace(
            policy,
            actions=(
                replace(policy.actions[0], reason="변경된 내부 대응 근거"),
            ),
        )

        generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=policy,
            guides=[self._guide()],
        )
        generator.generate(
            fraud_type="ACCOUNT_TAKEOVER",
            policy=changed_policy,
            guides=[self._guide()],
        )

        self.assertEqual(llm.calls, 2)

    def test_llm_failure_result_is_not_cached(self) -> None:
        llm = FakeStructuredLLM(error=RuntimeError("LLM 실패"))
        generator = RagResponsePlanGenerator(structured_llm=llm)
        metrics = {}

        for _ in range(2):
            generator.generate(
                fraud_type="ACCOUNT_TAKEOVER",
                policy=self._policy(),
                guides=[self._guide()],
                metrics=metrics,
            )

        self.assertEqual(llm.calls, 2)
        self.assertFalse(metrics["response_plan_cache_hit"])
        self.assertTrue(metrics["response_plan_fallback_used"])

    def test_concurrent_same_key_calls_llm_once(self) -> None:
        llm = FakeStructuredLLM(
            self._generated_plan(),
            delay_seconds=0.05,
        )
        generator = RagResponsePlanGenerator(structured_llm=llm)

        def generate_once():
            return generator.generate(
                fraud_type="ACCOUNT_TAKEOVER",
                policy=self._policy(),
                guides=[self._guide()],
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _index: generate_once(), range(4)))

        self.assertEqual(llm.calls, 1)
        self.assertTrue(all(result == results[0] for result in results))

    @staticmethod
    def _policy() -> ResponsePolicy:
        return ResponsePolicy(
            policy_id="POLICY-ACCOUNT-VERY-HIGH",
            fraud_type="ACCOUNT_TAKEOVER",
            risk_grade="VERY_HIGH",
            notification_required=True,
            notification_reason="고객 안내 필요",
            actions=(
                PolicyAction(
                    priority=1,
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    action="고객 본인 거래 여부 확인",
                    reason="원격제어 정황이 탐지되었다.",
                    required=True,
                ),
            ),
            checklist=(
                PolicyChecklistItem(
                    item_code="CUSTOMER_CONFIRMED",
                    label="고객 본인 거래 여부 확인",
                    required=True,
                ),
            ),
        )

    @staticmethod
    def _guide() -> RetrievedGuideChunkDTO:
        return RetrievedGuideChunkDTO(
            document_id="GUIDE-001",
            title="계정탈취 대응 절차",
            chunk_index=0,
            heading="고객 확인 절차",
            content="등록된 연락처로 고객에게 본인 거래 여부를 확인하는 대응 절차이다.",
            source_type="INTERNAL_MONITORING_GUIDE",
            source_name="FDShield",
            source_url=None,
            similarity_score=0.91,
            retrieval_rank=1,
        )

    @staticmethod
    def _generated_plan() -> GeneratedResponsePlan:
        return GeneratedResponsePlan(
            actions=[
                GeneratedActionDetail(
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    procedure_steps=["등록 연락처로 본인 거래 여부를 확인한다."],
                    cautions=["비밀번호와 인증번호를 요청하지 않는다."],
                )
            ]
        )


if __name__ == "__main__":
    unittest.main()
