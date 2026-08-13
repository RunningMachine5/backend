import unittest
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.domain.enums import RiskGrade
from app.domain.response_policy import (
    PolicyAction,
    PolicyChecklistItem,
    ResponsePolicy,
)
from app.dto.agent import (
    AgentInputDTO,
    AgentResponseDTO,
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
)
from app.dto.agent_guide import RetrievedGuideChunkDTO
from app.services.agent.case_service import AgentCaseStartResult
from app.services.agent.type_confidence import calculate_type_confidence
from app.services.agent.workflow import AgentWorkflow


class FakeCaseService:
    """Graph 분기 테스트에서 DB 사건 상태 변경 계약만 재현한다."""

    def __init__(
        self,
        rule_result: FraudTypeScoreResultDTO,
        *,
        created: bool = True,
    ) -> None:
        self.rule_result = rule_result
        self.created = created
        self.complete_calls = 0
        self.fail_calls = 0

    def start_case(self, agent_input: AgentInputDTO) -> AgentCaseStartResult:
        response = self._response(
            agent_input,
            execution_status=AgentExecutionStatus.PROCESSING,
        )
        return AgentCaseStartResult(
            response=response,
            type_confidence=calculate_type_confidence(
                self.rule_result.type_scores
            ),
            created=self.created,
        )

    def complete_case(
        self,
        case_id: str,
        *,
        investigation_result,
        similar_case_results,
        response_result,
        generation_metadata,
        best_similar_case_id=None,
    ) -> AgentResponseDTO:
        del similar_case_results, best_similar_case_id
        self.complete_calls += 1
        return AgentResponseDTO(
            case_id=case_id,
            transaction_id="TX-001",
            execution_status=AgentExecutionStatus.COMPLETED,
            failure_reason=None,
            rule_result=self.rule_result,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
            investigation_result=investigation_result,
            best_similar_case_id=None,
            similar_case_results=[],
            response_result=response_result,
            generation_metadata=generation_metadata,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            completed_at=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
        )

    def fail_case(
        self,
        case_id: str,
        *,
        failure_reason: str,
        generation_metadata,
    ) -> AgentResponseDTO:
        self.fail_calls += 1
        response = self._response(
            self._input(),
            execution_status=AgentExecutionStatus.FAILED,
        )
        return replace(
            response,
            case_id=case_id,
            failure_reason=failure_reason,
            generation_metadata=generation_metadata,
            completed_at=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
        )

    def _response(
        self,
        agent_input: AgentInputDTO,
        *,
        execution_status: AgentExecutionStatus,
    ) -> AgentResponseDTO:
        return AgentResponseDTO(
            case_id="CASE-20260812-TEST0001",
            transaction_id=agent_input.transaction_id,
            execution_status=execution_status,
            failure_reason=None,
            rule_result=self.rule_result,
            risk_score=agent_input.risk_score,
            risk_grade=agent_input.risk_grade,
            investigation_result=None,
            best_similar_case_id=None,
            similar_case_results=[],
            response_result=None,
            generation_metadata={},
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            completed_at=None,
        )

    @staticmethod
    def _input() -> AgentInputDTO:
        return AgentInputDTO(
            transaction_id="TX-001",
            fraud_type_score_result_id=7,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
        )


class FakePolicyRepository:
    def __init__(self) -> None:
        self.requested_fraud_type: str | None = None

    def get_response_policy(
        self,
        *,
        fraud_type: str,
        risk_grade: str,
    ) -> ResponsePolicy:
        self.requested_fraud_type = fraud_type
        return ResponsePolicy(
            policy_id=f"POLICY-{fraud_type}-{risk_grade}",
            fraud_type=fraud_type,
            risk_grade=risk_grade,
            notification_required=True,
            notification_reason="이상거래 고객 안내가 필요하다.",
            actions=(
                PolicyAction(
                    priority=1,
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    action="고객 본인 거래 여부를 확인한다.",
                    reason="이상거래로 탐지되었다.",
                    required=True,
                ),
            ),
            checklist=(
                PolicyChecklistItem(
                    item_code="CHECK_CUSTOMER_CONFIRMATION",
                    label="고객 본인 거래 여부 확인",
                    required=True,
                ),
            ),
        )


class FakeGuideSearchService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.last_request = None

    def search(self, request):
        self.last_request = request
        if self.error is not None:
            raise self.error
        return [
            RetrievedGuideChunkDTO(
                document_id="GUIDE-001",
                title="담당자 대응 절차",
                chunk_index=0,
                heading="고객 확인",
                content="고객에게 본인 거래 여부를 확인한다.",
                source_type="INTERNAL_DEMO_GUIDE",
                source_name="FDShield 내부 지침",
                source_url=None,
                similarity_score=0.91,
                retrieval_rank=1,
            )
        ]


class FakeInvestigator:
    def __init__(self, recommended_type: str) -> None:
        self.recommended_type = recommended_type
        self.call_count = 0

    def investigate(self, *, confidence, **_kwargs) -> InvestigationResultDTO:
        self.call_count += 1
        return InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=confidence.score_margin,
            investigation_status=InvestigationStatus.COMPLETED,
            recommended_fraud_type=self.recommended_type,
            recommendation_reason="공통 Rule 근거가 있는 완료 사건을 확인했다.",
            best_similarity_score=0.91,
            common_evidence_codes=["REMOTE_CONTROL_DETECTED"],
            confirmed_case_count=2,
        )


class AgentWorkflowTest(unittest.TestCase):
    def test_confident_case_skips_investigation_and_completes(self) -> None:
        investigator = FakeInvestigator("MESSENGER_PHISHING")
        case_service = FakeCaseService(self._rule_result(0.80, 0.40))
        policy_repository = FakePolicyRepository()
        guide_search = FakeGuideSearchService()
        workflow = AgentWorkflow(
            case_service=case_service,  # type: ignore[arg-type]
            policy_repository=policy_repository,
            guide_search_service=guide_search,  # type: ignore[arg-type]
            investigator=investigator,
        )

        state = workflow.run_state(self._input())
        response = state["final_response"]

        self.assertEqual(response.execution_status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(investigator.call_count, 0)
        self.assertEqual(policy_repository.requested_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(state["email_command"].primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(response.investigation_result.investigation_status, InvestigationStatus.NOT_REQUIRED)
        self.assertEqual(response.response_result.applied_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(case_service.complete_calls, 1)
        self.assertGreaterEqual(response.generation_metadata["total_latency_ms"], 0)
        self.assertEqual(response.generation_metadata["investigation_latency_ms"], 0)
        self.assertEqual(response.generation_metadata["react_llm_call_count"], 0)
        self.assertEqual(response.generation_metadata["api_attempt_count"], 0)
        self.assertEqual(response.generation_metadata["retry_count"], 0)
        self.assertEqual(response.generation_metadata["tool_call_count"], 0)
        self.assertFalse(response.generation_metadata["fallback_used"])
        self.assertIsNone(response.generation_metadata["fallback_reason"])

    def test_ambiguous_case_uses_investigator_recommendation(self) -> None:
        investigator = FakeInvestigator("MESSENGER_PHISHING")
        policy_repository = FakePolicyRepository()
        guide_search = FakeGuideSearchService()
        workflow = AgentWorkflow(
            case_service=FakeCaseService(self._rule_result(0.62, 0.57)),  # type: ignore[arg-type]
            policy_repository=policy_repository,
            guide_search_service=guide_search,  # type: ignore[arg-type]
            investigator=investigator,
        )

        state = workflow.run_state(self._input())

        self.assertEqual(investigator.call_count, 1)
        self.assertEqual(state["applied_fraud_type"], "MESSENGER_PHISHING")
        self.assertEqual(state["email_command"].primary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(policy_repository.requested_fraud_type, "MESSENGER_PHISHING")
        self.assertEqual(guide_search.last_request.fraud_type, "MESSENGER_PHISHING")

    def test_existing_case_returns_without_running_followup_nodes(self) -> None:
        case_service = FakeCaseService(
            self._rule_result(0.80, 0.40),
            created=False,
        )
        policy_repository = FakePolicyRepository()
        workflow = AgentWorkflow(
            case_service=case_service,  # type: ignore[arg-type]
            policy_repository=policy_repository,
            guide_search_service=FakeGuideSearchService(),  # type: ignore[arg-type]
        )

        response = workflow.run(self._input())

        self.assertEqual(response.execution_status, AgentExecutionStatus.PROCESSING)
        self.assertIsNone(policy_repository.requested_fraud_type)
        self.assertEqual(case_service.complete_calls, 0)

    def test_node_error_marks_created_case_as_failed(self) -> None:
        case_service = FakeCaseService(self._rule_result(0.80, 0.40))
        workflow = AgentWorkflow(
            case_service=case_service,  # type: ignore[arg-type]
            policy_repository=FakePolicyRepository(),
            guide_search_service=FakeGuideSearchService(  # type: ignore[arg-type]
                error=RuntimeError("검색 실패")
            ),
        )

        response = workflow.run(self._input())

        self.assertEqual(response.execution_status, AgentExecutionStatus.FAILED)
        self.assertEqual(response.failure_reason, "검색 실패")
        self.assertEqual(case_service.fail_calls, 1)

    @staticmethod
    def _input() -> AgentInputDTO:
        return AgentInputDTO(
            transaction_id="TX-001",
            fraud_type_score_result_id=7,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
        )

    @staticmethod
    def _rule_result(
        account_takeover: float,
        messenger_phishing: float,
    ) -> FraudTypeScoreResultDTO:
        return FraudTypeScoreResultDTO(
            fraud_type_score_result_id=7,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": account_takeover,
                "MESSENGER_PHISHING": messenger_phishing,
                "VOICE_PHISHING": 0.20,
                "FRAUD_USED_ACCOUNT": 0.10,
            },
            matched_components=[],
        )


if __name__ == "__main__":
    unittest.main()
