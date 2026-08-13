import unittest

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
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.messages = None

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.result


class RagResponsePlanGeneratorTest(unittest.TestCase):
    def test_rag_context_enriches_policy_actions(self) -> None:
        llm = FakeStructuredLLM(
            GeneratedResponsePlan(
                summary="원격제어 정황이 확인된 고위험 계정탈취 의심 사건이다.",
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
                summary="임의 계획",
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


if __name__ == "__main__":
    unittest.main()
