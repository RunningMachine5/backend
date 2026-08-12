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
    ResolvedCaseDetailDTO,
    SimilarResolvedCaseDTO,
)
from app.services.agent.similar_case_investigator import (
    DatabaseSimilarCaseTools,
    LimitedSimilarCaseInvestigator,
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


class LimitedSimilarCaseInvestigatorTest(unittest.TestCase):
    def test_no_similar_case_stops_without_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools([])
        result = self._investigate(tools)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(tools.search_calls, 1)
        self.assertEqual(tools.detail_calls, [])

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
        result = self._investigate(tools)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(result.confirmed_case_count, 2)
        self.assertEqual(tools.detail_calls, ["CASE-101", "CASE-102"])

    def test_one_strong_case_can_support_recommendation(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-201", 0.91)],
            {"CASE-201": "MESSENGER_PHISHING"},
        )
        result = self._investigate(tools)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "MESSENGER_PHISHING")
        self.assertEqual(tools.detail_calls, ["CASE-201"])

    def test_weak_cases_do_not_trigger_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-301", 0.70)],
            {"CASE-301": "ACCOUNT_TAKEOVER"},
        )
        result = self._investigate(tools)

        self.assertEqual(
            result.investigation_status,
            InvestigationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(tools.detail_calls, [])

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
    def _investigate(cls, tools: FakeSimilarCaseTools):
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
        return LimitedSimilarCaseInvestigator(tools).investigate(
            case_id="CASE-CURRENT",
            rule_result=rule_result,
            confidence=calculate_type_confidence(rule_result.type_scores),
            risk_score=90,
            risk_grade="VERY_HIGH",
        )


if __name__ == "__main__":
    unittest.main()
