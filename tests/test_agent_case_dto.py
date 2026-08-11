import unittest
from datetime import UTC, datetime

from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InformationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.domain.enums import RiskGrade
from app.dto.agent import (
    AgentResponseDTO,
    ChecklistItemDTO,
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    RecommendedActionDTO,
    ResponsePlanDTO,
    RuleEvidenceDTO,
    SimilarCaseResultDTO,
    dto_to_dict,
)
from app.services.agent.case_mapper import agent_response_from_dict


class AgentCaseDTOTest(unittest.TestCase):
    """Agent 중첩 DTO의 JSON 직렬화와 복원을 검증한다."""

    def test_similar_case_accepts_rank_and_score_boundaries(self) -> None:
        first = SimilarCaseResultDTO("CASE-001", 1, 0.0, "최소 유사도")
        third = SimilarCaseResultDTO("CASE-003", 3, 1.0, "최대 유사도")

        self.assertEqual(first.similarity_score, 0.0)
        self.assertEqual(third.similarity_score, 1.0)

    def test_nested_agent_response_round_trip(self) -> None:
        response = self._completed_response()

        serialized = dto_to_dict(response)
        restored = agent_response_from_dict(serialized)

        self.assertEqual(restored, response)
        self.assertEqual(serialized["execution_status"], "COMPLETED")
        self.assertEqual(serialized["risk_grade"], "VERY_HIGH")
        self.assertEqual(
            serialized["created_at"],
            "2026-08-11T12:00:00+00:00",
        )

    @staticmethod
    def _completed_response() -> AgentResponseDTO:
        rule_result = FraudTypeScoreResultDTO(
            fraud_type_score_result_id=7,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": 0.62,
                "MESSENGER_PHISHING": 0.57,
            },
            matched_components=[
                RuleEvidenceDTO(
                    fraud_type="ACCOUNT_TAKEOVER",
                    evidence_code="remote_control",
                    observed_value=True,
                    contribution=0.15,
                )
            ],
        )
        investigation = InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=0.05,
            investigation_status=InvestigationStatus.COMPLETED,
            recommended_fraud_type="ACCOUNT_TAKEOVER",
            recommendation_reason="공통 Rule 근거가 확인되었다.",
            best_similarity_score=0.91,
            common_evidence_codes=["ACCOUNT_TAKEOVER:remote_control"],
            confirmed_case_count=2,
        )
        response_plan = ResponsePlanDTO(
            applied_fraud_type="ACCOUNT_TAKEOVER",
            information_status=InformationStatus.SUFFICIENT,
            summary="계정탈취 의심 사건 대응 계획이다.",
            recommended_actions=[
                RecommendedActionDTO(
                    priority=1,
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    action="고객 본인 거래 여부를 확인한다.",
                    reason="원격제어 정황이 확인되었다.",
                    required=True,
                    procedure_steps=["등록 연락처로 고객에게 연락한다."],
                    cautions=["의심 거래 정보를 먼저 노출하지 않는다."],
                )
            ],
            checklist=[
                ChecklistItemDTO(
                    item_code="CUSTOMER_CONFIRMED",
                    label="고객 본인 거래 여부 확인",
                    required=True,
                )
            ],
        )
        created_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        return AgentResponseDTO(
            case_id="CASE-001",
            transaction_id="TX-001",
            execution_status=AgentExecutionStatus.COMPLETED,
            failure_reason=None,
            rule_result=rule_result,
            risk_score=92,
            risk_grade=RiskGrade.VERY_HIGH,
            investigation_result=investigation,
            best_similar_case_id="CASE-HISTORY-001",
            similar_case_results=[
                SimilarCaseResultDTO(
                    similar_case_id="CASE-HISTORY-001",
                    similarity_rank=1,
                    similarity_score=0.91,
                    similarity_reason="원격제어 Rule 근거가 동일하다.",
                )
            ],
            response_result=response_plan,
            generation_metadata={"duration_ms": 850},
            created_at=created_at,
            completed_at=created_at,
        )


if __name__ == "__main__":
    unittest.main()
