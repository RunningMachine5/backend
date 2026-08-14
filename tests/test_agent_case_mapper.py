import unittest

from app.data.model.agent import AgentCase
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudTypeScoreResult,
)
from app.data.model.transaction import Transaction
from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InformationStatus,
    InvestigationStatus,
)
from app.domain.enums import RiskGrade
from app.services.agent.case_mapper import (
    build_agent_input_dto,
    to_agent_response_dto,
    to_fraud_type_score_result_dto,
)


class AgentCaseMapperTest(unittest.TestCase):
    """Rule 및 Agent ORM 모델이 확정 DTO로 변환되는지 검증한다."""

    def setUp(self) -> None:
        self.score_result = FraudTypeScoreResult(
            id=7,
            transaction_id=1,
            rule_set_id=1,
            rule_filter_status="APPLIED",
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": 0.62,
                "MESSENGER_PHISHING": 0.57,
            },
            matched_components={
                "ACCOUNT_TAKEOVER": ["remote_control"],
                "MESSENGER_PHISHING": [],
            },
        )
        self.rules = [
            FraudRule(
                id=11,
                rule_set_id=1,
                type_code="ACCOUNT_TAKEOVER",
                display_name="계정탈취",
            ),
            FraudRule(
                id=12,
                rule_set_id=1,
                type_code="MESSENGER_PHISHING",
                display_name="메신저피싱",
            ),
        ]
        self.components = [
            FraudRuleComponent(
                id=21,
                rule_id=11,
                component_key="remote_control",
                name="원격제어",
                condition_expression={
                    "field": "remote_control",
                    "operator": "EQ",
                    "value": True,
                },
                weight=0.15,
            )
        ]

    def test_rule_result_uses_component_weight_as_contribution(self) -> None:
        result = to_fraud_type_score_result_dto(
            self.score_result,
            rules=self.rules,
            components=self.components,
        )

        self.assertEqual(result.fraud_type_score_result_id, 7)
        self.assertEqual(len(result.matched_components), 1)
        evidence = result.matched_components[0]
        self.assertEqual(evidence.fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(evidence.evidence_code, "remote_control")
        self.assertIs(evidence.observed_value, True)
        self.assertEqual(evidence.contribution, 0.15)

    def test_rule_result_rejects_unknown_matched_component(self) -> None:
        self.score_result.matched_components = {
            "ACCOUNT_TAKEOVER": ["unknown_component"]
        }

        with self.assertRaisesRegex(ValueError, "가중치를 찾을 수 없다"):
            to_fraud_type_score_result_dto(
                self.score_result,
                rules=self.rules,
                components=self.components,
            )

    def test_agent_input_requires_same_transaction(self) -> None:
        transaction = Transaction.model_construct(id=2)

        with self.assertRaisesRegex(ValueError, "transaction_id"):
            build_agent_input_dto(
                transaction,
                self.score_result,
                risk_score=80,
                risk_grade=RiskGrade.HIGH,
            )

    def test_agent_input_validates_risk_score_boundaries(self) -> None:
        transaction = Transaction.model_construct(id=1)

        for risk_score in (0, 100):
            dto = build_agent_input_dto(
                transaction,
                self.score_result,
                risk_score=risk_score,
                risk_grade=RiskGrade.HIGH,
            )
            self.assertEqual(dto.risk_score, risk_score)

        for risk_score in (-1, 101):
            with self.assertRaises(ValueError):
                build_agent_input_dto(
                    transaction,
                    self.score_result,
                    risk_score=risk_score,
                    risk_grade=RiskGrade.HIGH,
                )

    def test_completed_agent_case_maps_nested_json(self) -> None:
        agent_case = AgentCase(
            case_id="CASE-001",
            transaction_id=1,
            fraud_type_score_result_id=7,
            execution_status="COMPLETED",
            risk_score=92,
            risk_grade="VERY_HIGH",
            investigation_result={
                "classification_status": "AMBIGUOUS",
                "score_margin": 0.05,
                "investigation_status": "COMPLETED",
                "recommended_fraud_type": "ACCOUNT_TAKEOVER",
                "recommendation_reason": "유사 완료 사건 근거가 충분하다.",
                "best_similarity_score": 0.91,
                "common_evidence_codes": [
                    "ACCOUNT_TAKEOVER:remote_control"
                ],
                "confirmed_case_count": 2,
            },
            best_similar_case_id="CASE-HISTORY-001",
            similar_case_results=[
                {
                    "similar_case_id": "CASE-HISTORY-001",
                    "similarity_rank": 1,
                    "similarity_score": 0.91,
                    "similarity_reason": "원격제어 근거가 동일하다.",
                }
            ],
            response_result={
                "applied_fraud_type": "ACCOUNT_TAKEOVER",
                "information_status": "SUFFICIENT",
                "summary": "계정탈취 대응 계획이다.",
                "recommended_actions": [
                    {
                        "priority": 1,
                        "action_code": "VERIFY_CUSTOMER_TRANSACTION",
                        "action": "고객 본인 거래 여부를 확인한다.",
                        "reason": "원격제어 정황이 확인되었다.",
                        "required": True,
                        "procedure_steps": ["고객에게 연락한다."],
                        "cautions": ["개인정보를 노출하지 않는다."],
                    }
                ],
                "checklist": [
                    {
                        "item_code": "CUSTOMER_CONFIRMED",
                        "label": "고객 본인 거래 여부 확인",
                        "required": True,
                    }
                ],
            },
            generation_metadata={"duration_ms": 850},
        )

        response = to_agent_response_dto(
            agent_case,
            self.score_result,
            rules=self.rules,
            components=self.components,
        )

        self.assertEqual(response.execution_status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(response.risk_grade, RiskGrade.VERY_HIGH)
        self.assertEqual(
            response.investigation_result.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )
        self.assertEqual(
            response.investigation_result.investigation_status,
            InvestigationStatus.COMPLETED,
        )
        self.assertEqual(
            response.response_result.information_status,
            InformationStatus.SUFFICIENT,
        )
        self.assertEqual(len(response.response_result.recommended_actions), 1)
        self.assertEqual(len(response.similar_case_results), 1)

    def test_processing_case_allows_empty_results(self) -> None:
        agent_case = AgentCase(
            case_id="CASE-002",
            transaction_id=1,
            fraud_type_score_result_id=7,
            execution_status="PROCESSING",
            risk_score=70,
            risk_grade="HIGH",
        )

        response = to_agent_response_dto(
            agent_case,
            self.score_result,
            rules=self.rules,
            components=self.components,
        )

        self.assertIsNone(response.investigation_result)
        self.assertIsNone(response.response_result)
        self.assertEqual(response.similar_case_results, [])

    def test_failed_case_preserves_failure_reason(self) -> None:
        agent_case = AgentCase(
            case_id="CASE-FAILED",
            transaction_id=1,
            fraud_type_score_result_id=7,
            execution_status="FAILED",
            failure_reason="유사 사건 조회 시간이 초과되었다.",
            risk_score=70,
            risk_grade="HIGH",
        )

        response = to_agent_response_dto(
            agent_case,
            self.score_result,
            rules=self.rules,
            components=self.components,
        )

        self.assertEqual(response.execution_status, AgentExecutionStatus.FAILED)
        self.assertEqual(
            response.failure_reason,
            "유사 사건 조회 시간이 초과되었다.",
        )


if __name__ == "__main__":
    unittest.main()
