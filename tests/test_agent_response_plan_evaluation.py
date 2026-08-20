import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.domain.agent_status import InformationStatus
from app.domain.response_policy import PolicyAction, PolicyChecklistItem, ResponsePolicy
from app.dto.agent import ChecklistItemDTO, RecommendedActionDTO, ResponsePlanDTO
from app.dto.agent_guide import RetrievedGuideChunkDTO
from app.services.agent.response_plan_evaluation import (
    ResponsePlanEvaluationCase,
    evaluate_response_plans,
    load_response_plan_evaluation_cases,
)
from app.services.agent.response_policy import (
    YamlPolicyRepository,
    get_default_policy_repository,
)


class FakeGuideSearcher:
    def __init__(self) -> None:
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        return [
            RetrievedGuideChunkDTO(
                document_id="GUIDE-001",
                title="계정탈취 대응 절차",
                chunk_index=0,
                heading="고객 확인",
                content="등록 연락처로 고객의 본인 거래 여부를 확인한다.",
                source_type="INTERNAL_DEMO_GUIDE",
                source_name="FDShield",
                source_url=None,
                similarity_score=0.91,
                retrieval_rank=1,
            )
        ]


class FakePlanGenerator:
    def __init__(self, *, enriched: bool) -> None:
        self.enriched = enriched

    def generate(self, *, fraud_type, policy, guides):
        return ResponsePlanDTO(
            applied_fraud_type=fraud_type,
            information_status=InformationStatus.SUFFICIENT,
            summary="계정탈취 의심 사건의 대응 계획이다.",
            recommended_actions=[
                RecommendedActionDTO(
                    priority=action.priority,
                    action_code=action.action_code,
                    action=action.action,
                    reason=action.reason,
                    required=action.required,
                    procedure_steps=["등록 연락처로 고객에게 확인한다."]
                    if self.enriched
                    else [],
                    cautions=["인증번호를 요청하지 않는다."]
                    if self.enriched
                    else [],
                )
                for action in policy.actions
            ],
            checklist=[
                ChecklistItemDTO(
                    item_code=item.item_code,
                    label=item.label,
                    required=item.required,
                )
                for item in policy.checklist
            ],
        )


class ResponsePlanEvaluationTest(unittest.TestCase):
    def test_policy_only_and_rag_llm_metrics_are_compared(self) -> None:
        searcher = FakeGuideSearcher()
        report = evaluate_response_plans(
            (
                ResponsePlanEvaluationCase(
                    case_id="PLAN-001",
                    fraud_type="ACCOUNT_TAKEOVER",
                    risk_grade="HIGH",
                    query="계정탈취 고객 확인 절차",
                ),
            ),
            policy_repository=YamlPolicyRepository((self._policy(),)),
            guide_searcher=searcher,
            policy_generator=FakePlanGenerator(enriched=False),
            rag_generator=FakePlanGenerator(enriched=True),
            repeat=3,
            top_k=3,
        )

        self.assertEqual(report.policy_only.procedure_coverage, 0.0)
        self.assertEqual(report.rag_llm.procedure_coverage, 1.0)
        self.assertEqual(report.rag_llm.caution_coverage, 1.0)
        self.assertEqual(report.rag_llm.required_action_coverage, 1.0)
        self.assertEqual(report.rag_llm.action_code_precision, 1.0)
        self.assertEqual(report.rag_llm.fallback_rate, 0.0)
        self.assertEqual(report.rag_llm.case_count, 3)
        self.assertEqual(len(report.results), 6)
        self.assertEqual(searcher.requests[0].top_k, 3)
        self.assertEqual(
            [result.run_number for result in report.results if result.strategy == "RAG_LLM"],
            [1, 2, 3],
        )

    def test_evaluation_cases_are_loaded_from_yaml(self) -> None:
        content = """
cases:
  - case_id: PLAN-001
    fraud_type: ACCOUNT_TAKEOVER
    risk_grade: HIGH
    query: 계정탈취 대응 절차
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.yaml"
            path.write_text(content, encoding="utf-8")
            cases = load_response_plan_evaluation_cases(path)

        self.assertEqual(cases[0].case_id, "PLAN-001")
        self.assertEqual(cases[0].risk_grade, "HIGH")

    def test_default_cases_match_existing_response_policies(self) -> None:
        cases = load_response_plan_evaluation_cases()
        repository = get_default_policy_repository()

        self.assertEqual(len(cases), 16)
        self.assertEqual(
            Counter((case.fraud_type, case.risk_grade) for case in cases),
            {
                (fraud_type, risk_grade): 1
                for fraud_type in (
                    "VOICE_PHISHING",
                    "MESSENGER_PHISHING",
                    "ACCOUNT_TAKEOVER",
                    "FRAUD_USED_ACCOUNT",
                )
                for risk_grade in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
            },
        )
        for case in cases:
            policy = repository.get_response_policy(
                fraud_type=case.fraud_type,
                risk_grade=case.risk_grade,
            )
            self.assertEqual(policy.fraud_type, case.fraud_type)
            self.assertEqual(policy.risk_grade, case.risk_grade)

    @staticmethod
    def _policy() -> ResponsePolicy:
        return ResponsePolicy(
            policy_id="POLICY-ACCOUNT-HIGH",
            fraud_type="ACCOUNT_TAKEOVER",
            risk_grade="HIGH",
            notification_required=True,
            notification_reason="고객 확인 필요",
            actions=(
                PolicyAction(
                    priority=1,
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    action="고객 본인 거래 여부 확인",
                    reason="계정탈취 여부 확인이 필요하다.",
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


if __name__ == "__main__":
    unittest.main()
