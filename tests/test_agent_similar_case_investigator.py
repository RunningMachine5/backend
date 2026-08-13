import unittest
from types import SimpleNamespace

from app.domain.agent_status import (
    ClassificationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.dto.agent import (
    FraudTypeScoreResultDTO,
    RuleEvidenceDTO,
)
from app.dto.agent_investigation import (
    InvestigationAction,
    InvestigationActionDTO,
    ResolvedCaseDetailDTO,
    SimilarResolvedCaseDTO,
)
from app.services.agent.similar_case_investigator import (
    DatabaseSimilarCaseTools,
    InvestigationActionOutput,
    LimitedSimilarCaseInvestigator,
    OpenAIInvestigationActionSelector,
)
from app.services.agent.type_confidence import calculate_type_confidence


class FakeSimilarCaseTools:
    """조사 경로에 따라 Tool 호출 수가 달라지는지 기록한다."""

    def __init__(
        self,
        search_results: list[SimilarResolvedCaseDTO],
        confirmed_types: dict[str, str] | None = None,
    ) -> None:
        self.search_results = search_results
        self.confirmed_types = confirmed_types or {}
        self.search_calls = 0
        self.detail_calls: list[str] = []

    def search_similar_resolved_cases(self, **_kwargs):
        self.search_calls += 1
        return self.search_results

    def get_resolved_case_detail(self, case_id: str) -> ResolvedCaseDetailDTO:
        self.detail_calls.append(case_id)
        return ResolvedCaseDetailDTO(
            case_id=case_id,
            confirmed_fraud_type=self.confirmed_types[case_id],
        )


class FakeActionSelector:
    """테스트에서 LLM의 구조화 Action 응답을 순서대로 재현한다."""

    def __init__(self, actions: list[InvestigationActionDTO] | None = None) -> None:
        self.actions = list(actions or [])
        self.call_count = 0
        self.error: Exception | None = None

    def select_action(self, **_kwargs) -> InvestigationActionDTO:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return self.actions.pop(0)


class FakeInvestigationRepository:
    def __init__(self) -> None:
        self.case = SimpleNamespace(
            case_id="CASE-PAST",
            risk_score=88,
            risk_grade="VERY_HIGH",
        )
        self.score = SimpleNamespace(
            type_scores={
                "ACCOUNT_TAKEOVER": 0.61,
                "MESSENGER_PHISHING": 0.56,
                "VOICE_PHISHING": 0.20,
                "FRAUD_USED_ACCOUNT": 0.10,
            },
            matched_components={"ACCOUNT_TAKEOVER": ["REMOTE_CONTROL"]},
        )
        self.review = SimpleNamespace(
            confirmed_fraud_type="ACCOUNT_TAKEOVER",
        )

    def list_resolved_cases(self, **_kwargs):
        return [(self.case, self.score)]

    def get_resolved_review(self, case_id: str):
        return self.review if case_id == self.case.case_id else None


class DatabaseSimilarCaseToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = DatabaseSimilarCaseTools(
            FakeInvestigationRepository()  # type: ignore[arg-type]
        )

    def test_search_maps_reviewed_case_and_similarity_result(self) -> None:
        results = self.tools.search_similar_resolved_cases(
            current_case_id="CASE-CURRENT",
            candidate_fraud_types=("ACCOUNT_TAKEOVER", "MESSENGER_PHISHING"),
            type_scores={
                "ACCOUNT_TAKEOVER": 0.62,
                "MESSENGER_PHISHING": 0.57,
                "VOICE_PHISHING": 0.20,
                "FRAUD_USED_ACCOUNT": 0.10,
            },
            evidence=[
                RuleEvidenceDTO(
                    fraud_type="ACCOUNT_TAKEOVER",
                    evidence_code="REMOTE_CONTROL",
                    observed_value=True,
                    contribution=0.30,
                )
            ],
            risk_score=90,
            risk_grade="VERY_HIGH",
            top_k=5,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].case_id, "CASE-PAST")
        self.assertGreater(results[0].similarity_score, 0.90)

    def test_detail_maps_confirmed_type(self) -> None:
        detail = self.tools.get_resolved_case_detail("CASE-PAST")

        self.assertEqual(detail.confirmed_fraud_type, "ACCOUNT_TAKEOVER")


class FakeStructuredLLM:
    def __init__(self) -> None:
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return InvestigationActionOutput(
            action=InvestigationAction.INSPECT_CASE,
            case_id="CASE-PAST",
            recommended_fraud_type=None,
            reason="공통 Rule 근거와 유사도가 높다.",
        )


class OpenAIInvestigationActionSelectorTest(unittest.TestCase):
    def test_structured_llm_response_maps_to_action_dto(self) -> None:
        structured_llm = FakeStructuredLLM()
        selector = OpenAIInvestigationActionSelector(
            structured_llm=structured_llm,
        )

        action = selector.select_action(
            candidate_fraud_types=("ACCOUNT_TAKEOVER", "MESSENGER_PHISHING"),
            similar_cases=[
                SimilarResolvedCaseDTO(
                    case_id="CASE-PAST",
                    similarity_score=0.91,
                    common_evidence_codes=("ACCOUNT_TAKEOVER:REMOTE_CONTROL",),
                )
            ],
            inspected_cases=[],
            remaining_detail_calls=2,
        )

        self.assertEqual(action.action, InvestigationAction.INSPECT_CASE)
        self.assertEqual(action.case_id, "CASE-PAST")
        self.assertEqual(structured_llm.messages[0]["role"], "system")


class LimitedSimilarCaseInvestigatorTest(unittest.TestCase):
    def test_no_similar_case_stops_without_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools([])
        selector = FakeActionSelector()
        result = self._investigate(tools, selector)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(tools.search_calls, 1)
        self.assertEqual(tools.detail_calls, [])
        self.assertEqual(selector.call_count, 0)

    def test_two_supported_cases_complete_recommendation(self) -> None:
        tools = FakeSimilarCaseTools(
            [
                self._case("CASE-101", 0.88),
                self._case("CASE-102", 0.82),
                self._case("CASE-103", 0.76),
            ],
            {
                "CASE-101": "ACCOUNT_TAKEOVER",
                "CASE-102": "ACCOUNT_TAKEOVER",
                "CASE-103": "MESSENGER_PHISHING",
            },
        )
        selector = FakeActionSelector(
            [
                self._action(InvestigationAction.INSPECT_CASE, case_id="CASE-101"),
                self._action(InvestigationAction.INSPECT_CASE, case_id="CASE-102"),
                self._action(
                    InvestigationAction.STOP_RECOMMEND,
                    recommended_type="ACCOUNT_TAKEOVER",
                ),
            ]
        )
        result = self._investigate(tools, selector)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(result.confirmed_case_count, 2)
        self.assertEqual(tools.detail_calls, ["CASE-101", "CASE-102"])

    def test_one_strong_case_can_support_recommendation(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-201", 0.91)],
            {"CASE-201": "MESSENGER_PHISHING"},
        )
        selector = FakeActionSelector(
            [
                self._action(InvestigationAction.INSPECT_CASE, case_id="CASE-201"),
                self._action(
                    InvestigationAction.STOP_RECOMMEND,
                    recommended_type="MESSENGER_PHISHING",
                ),
            ]
        )
        result = self._investigate(tools, selector)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "MESSENGER_PHISHING")
        self.assertEqual(tools.detail_calls, ["CASE-201"])

    def test_weak_cases_do_not_trigger_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-301", 0.70)],
            {"CASE-301": "ACCOUNT_TAKEOVER"},
        )
        selector = FakeActionSelector()
        result = self._investigate(tools, selector)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(tools.detail_calls, [])
        self.assertEqual(selector.call_count, 0)

    def test_unlisted_case_selected_by_llm_is_rejected(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-401", 0.90)],
            {"CASE-401": "ACCOUNT_TAKEOVER"},
        )
        selector = FakeActionSelector(
            [self._action(InvestigationAction.INSPECT_CASE, case_id="CASE-UNKNOWN")]
        )

        result = self._investigate(tools, selector)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(tools.detail_calls, [])

    def test_llm_failure_falls_back_without_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-501", 0.90)],
            {"CASE-501": "ACCOUNT_TAKEOVER"},
        )
        selector = FakeActionSelector()
        selector.error = RuntimeError("LLM timeout")

        result = self._investigate(tools, selector)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertIn("Rule 1순위", result.recommendation_reason)
        self.assertEqual(tools.detail_calls, [])

    def test_investigation_records_generation_metrics(self) -> None:
        tools = FakeSimilarCaseTools([])
        selector = FakeActionSelector()
        metrics: dict[str, object] = {}

        self._investigate(tools, selector, metrics=metrics)

        self.assertGreaterEqual(metrics["investigation_latency_ms"], 0)
        self.assertEqual(metrics["react_llm_call_count"], 0)
        self.assertEqual(metrics["api_attempt_count"], 0)
        self.assertEqual(metrics["retry_count"], 0)
        self.assertEqual(metrics["tool_call_count"], 1)
        self.assertTrue(metrics["fallback_used"])
        self.assertIsNotNone(metrics["fallback_reason"])

    @staticmethod
    def _action(
        action: InvestigationAction,
        *,
        case_id: str | None = None,
        recommended_type: str | None = None,
    ) -> InvestigationActionDTO:
        return InvestigationActionDTO(
            action=action,
            case_id=case_id,
            recommended_fraud_type=recommended_type,
            reason="테스트 조사 판단이다.",
        )

    @staticmethod
    def _case(
        case_id: str,
        similarity_score: float,
    ) -> SimilarResolvedCaseDTO:
        return SimilarResolvedCaseDTO(
            case_id=case_id,
            similarity_score=similarity_score,
            common_evidence_codes=("ACCOUNT_TAKEOVER:REMOTE_CONTROL",),
        )

    @classmethod
    def _investigate(
        cls,
        tools: FakeSimilarCaseTools,
        selector: FakeActionSelector,
        *,
        metrics: dict[str, object] | None = None,
    ):
        rule_result = FraudTypeScoreResultDTO(
            fraud_type_score_result_id=1,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": 0.62,
                "MESSENGER_PHISHING": 0.57,
                "VOICE_PHISHING": 0.20,
                "FRAUD_USED_ACCOUNT": 0.10,
            },
            matched_components=[
                RuleEvidenceDTO(
                    fraud_type="ACCOUNT_TAKEOVER",
                    evidence_code="REMOTE_CONTROL",
                    observed_value=True,
                    contribution=0.30,
                )
            ],
        )
        return LimitedSimilarCaseInvestigator(tools, selector).investigate(
            case_id="CASE-CURRENT",
            rule_result=rule_result,
            confidence=calculate_type_confidence(rule_result.type_scores),
            risk_score=90,
            risk_grade="VERY_HIGH",
            metrics=metrics,
        )


if __name__ == "__main__":
    unittest.main()
