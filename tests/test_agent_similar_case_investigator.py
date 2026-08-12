import unittest
from datetime import UTC, datetime
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

    def __init__(self, search_results: list[SimilarResolvedCaseDTO]) -> None:
        self.search_results = search_results
        self.search_calls = 0
        self.detail_calls: list[str] = []
        self.transaction_calls = 0

    def search_similar_resolved_cases(self, **_kwargs):
        self.search_calls += 1
        return self.search_results

    def get_resolved_case_detail(self, case_id: str) -> ResolvedCaseDetailDTO:
        self.detail_calls.append(case_id)
        matched = next(case for case in self.search_results if case.case_id == case_id)
        return ResolvedCaseDetailDTO(
            case_id=case_id,
            transaction_id=matched.transaction_id,
            confirmed_fraud_type=matched.confirmed_fraud_type,
            performed_actions=[{"action_code": "VERIFY_CUSTOMER"}],
            checklist_results=[{"item_code": "CUSTOMER_CONFIRMED"}],
            resolution_summary="담당자가 사기 유형을 확정하고 고객 확인을 수행했다.",
            response_result={"applied_fraud_type": matched.confirmed_fraud_type},
        )

    def get_transaction_context(self, transaction_id: str):
        del transaction_id
        self.transaction_calls += 1
        raise AssertionError("기본 조사 경로에서는 거래 문맥을 호출하지 않아야 한다.")


class FakeInvestigationRepository:
    def __init__(self) -> None:
        self.case = SimpleNamespace(
            case_id="CASE-PAST",
            transaction_id="TX-PAST",
            risk_score=88,
            risk_grade="VERY_HIGH",
            response_result={"applied_fraud_type": "ACCOUNT_TAKEOVER"},
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
            performed_actions=[{"action_code": "VERIFY_CUSTOMER"}],
            checklist_results=[{"item_code": "CUSTOMER_CONFIRMED"}],
            resolution_summary="계정탈취 사기로 확정했다.",
        )
        self.transaction = SimpleNamespace(
            transaction_id="TX-CURRENT",
            transaction_amount=10_000_000,
            channel="mobile",
            transaction_datetime=datetime(2026, 8, 12, tzinfo=UTC),
            location="서울특별시",
        )

    def list_resolved_cases(self, **_kwargs):
        return [(self.case, self.score, self.review)]

    def get_resolved_case(self, case_id: str):
        return (self.case, self.review) if case_id == self.case.case_id else None

    def get_transaction(self, transaction_id: str):
        return self.transaction if transaction_id == self.transaction.transaction_id else None


class DatabaseSimilarCaseToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = DatabaseSimilarCaseTools(FakeInvestigationRepository())  # type: ignore[arg-type]

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
        self.assertEqual(results[0].confirmed_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertGreater(results[0].similarity_score, 0.90)

    def test_detail_and_transaction_context_map_minimum_fields(self) -> None:
        detail = self.tools.get_resolved_case_detail("CASE-PAST")
        context = self.tools.get_transaction_context("TX-CURRENT")

        self.assertEqual(detail.performed_actions[0]["action_code"], "VERIFY_CUSTOMER")
        self.assertEqual(context.transaction_amount, 10_000_000)
        self.assertEqual(context.channel, "mobile")


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
                self._case("CASE-101", "ACCOUNT_TAKEOVER", 0.88),
                self._case("CASE-102", "ACCOUNT_TAKEOVER", 0.82),
                self._case("CASE-103", "MESSENGER_PHISHING", 0.76),
            ]
        )
        result = self._investigate(tools)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(result.confirmed_case_count, 2)
        self.assertEqual(tools.detail_calls, ["CASE-101", "CASE-102"])
        self.assertEqual(tools.transaction_calls, 0)

    def test_one_strong_case_can_support_recommendation(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-201", "MESSENGER_PHISHING", 0.91)]
        )
        result = self._investigate(tools)

        self.assertEqual(result.investigation_status, InvestigationStatus.COMPLETED)
        self.assertEqual(result.recommended_fraud_type, "MESSENGER_PHISHING")
        self.assertEqual(tools.detail_calls, ["CASE-201"])

    def test_weak_cases_do_not_trigger_detail_lookup(self) -> None:
        tools = FakeSimilarCaseTools(
            [self._case("CASE-301", "ACCOUNT_TAKEOVER", 0.70)]
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
        fraud_type: str,
        similarity_score: float,
    ) -> SimilarResolvedCaseDTO:
        return SimilarResolvedCaseDTO(
            case_id=case_id,
            transaction_id=f"TX-{case_id}",
            confirmed_fraud_type=fraud_type,
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
            transaction_id="TX-CURRENT",
            rule_result=rule_result,
            confidence=calculate_type_confidence(rule_result.type_scores),
            risk_score=90,
            risk_grade="VERY_HIGH",
        )


if __name__ == "__main__":
    unittest.main()
