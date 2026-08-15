import unittest

from app.domain.agent_status import RuleFilterStatus
from app.dto.agent import FraudTypeScoreResultDTO, RuleEvidenceDTO
from app.dto.agent_investigation import SimilarResolvedCaseDTO
from app.services.agent.dashboard_similar_cases import DashboardSimilarCaseService


class FakeSimilarCaseTools:
    def __init__(self, cases: list[SimilarResolvedCaseDTO]) -> None:
        self.cases = cases
        self.last_request = None

    def search_similar_resolved_cases(self, **kwargs):
        self.last_request = kwargs
        return self.cases[: kwargs["top_k"]]

    def get_resolved_case_detail(self, case_id: str):
        raise NotImplementedError(case_id)


class DashboardSimilarCaseServiceTest(unittest.TestCase):
    def test_returns_ranked_top_three_with_evidence_reason(self) -> None:
        tools = FakeSimilarCaseTools(
            [
                SimilarResolvedCaseDTO(
                    case_id=f"CASE-{index}",
                    confirmed_fraud_type="ACCOUNT_TAKEOVER",
                    similarity_score=score,
                    common_evidence_codes=(
                        "ACCOUNT_TAKEOVER:REMOTE_CONTROL",
                    ),
                )
                for index, score in enumerate((0.93, 0.88, 0.81), start=1)
            ]
        )
        service = DashboardSimilarCaseService(tools)  # type: ignore[arg-type]

        results = service.find_top_three(
            current_case_id="CASE-CURRENT",
            rule_result=self._rule_result(),
            risk_score=90,
            risk_grade="VERY_HIGH",
        )

        self.assertEqual([item.similarity_rank for item in results], [1, 2, 3])
        self.assertEqual(results[0].similar_case_id, "CASE-1")
        self.assertIn("REMOTE_CONTROL", results[0].similarity_reason)
        self.assertEqual(tools.last_request["top_k"], 3)
        self.assertEqual(
            set(tools.last_request["candidate_fraud_types"]),
            set(self._rule_result().type_scores),
        )

    def test_returns_empty_list_when_no_resolved_case_exists(self) -> None:
        service = DashboardSimilarCaseService(  # type: ignore[arg-type]
            FakeSimilarCaseTools([])
        )

        results = service.find_top_three(
            current_case_id="CASE-CURRENT",
            rule_result=self._rule_result(),
            risk_score=90,
            risk_grade="VERY_HIGH",
        )

        self.assertEqual(results, [])

    @staticmethod
    def _rule_result() -> FraudTypeScoreResultDTO:
        return FraudTypeScoreResultDTO(
            fraud_type_score_result_id=7,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": 0.80,
                "MESSENGER_PHISHING": 0.40,
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


if __name__ == "__main__":
    unittest.main()
