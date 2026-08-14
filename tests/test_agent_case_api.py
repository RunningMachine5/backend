import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.api.agent_case import (
    AgentCaseCreateRequest,
    _public_response,
    create_agent_case,
    get_agent_case,
    get_agent_case_by_transaction,
)
from app.domain.agent_status import AgentExecutionStatus, RuleFilterStatus
from app.domain.enums import RiskGrade
from app.dto.agent import AgentResponseDTO, FraudTypeScoreResultDTO
from app.services.agent.case_service import AgentCaseNotFoundError


def _agent_response() -> AgentResponseDTO:
    return AgentResponseDTO(
        case_id="CASE-20260813-TEST0001",
        transaction_id=1,
        execution_status=AgentExecutionStatus.COMPLETED,
        failure_reason=None,
        rule_result=FraudTypeScoreResultDTO(
            fraud_type_score_result_id=7,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type="ACCOUNT_TAKEOVER",
            type_scores={
                "ACCOUNT_TAKEOVER": 0.8,
                "MESSENGER_PHISHING": 0.2,
            },
            matched_components=[],
        ),
        risk_score=76,
        risk_grade=RiskGrade.HIGH,
        investigation_result=None,
        best_similar_case_id=None,
        similar_case_results=[],
        response_result=None,
        generation_metadata={"total_latency_ms": 120},
        created_at=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 13, 10, 0, 1, tzinfo=UTC),
    )


class FakeWorkflow:
    def __init__(self) -> None:
        self.agent_input = None

    def run(self, agent_input):
        self.agent_input = agent_input
        return _agent_response()


class FakeCaseService:
    def __init__(self, *, missing: bool = False) -> None:
        self.missing = missing

    def get_case(self, case_id: str) -> AgentResponseDTO:
        if self.missing:
            raise AgentCaseNotFoundError(case_id)
        return _agent_response()

    def get_case_by_transaction(self, transaction_id: int) -> AgentResponseDTO:
        if self.missing:
            raise AgentCaseNotFoundError(transaction_id)
        return _agent_response()


class AgentCaseApiTest(unittest.TestCase):
    def test_create_case_builds_input_from_saved_detection_result(self) -> None:
        score_result = SimpleNamespace(id=7)
        prediction = SimpleNamespace(predict_proba=0.9)
        transaction = SimpleNamespace(transaction_amount=10_000_000)
        session = MagicMock()
        session.exec.return_value.first.side_effect = [score_result, prediction]
        session.get.return_value = transaction
        workflow = FakeWorkflow()

        response = create_agent_case(
            AgentCaseCreateRequest(transaction_id=1),
            session,
            workflow,
        )

        self.assertEqual(response.case_id, "CASE-20260813-TEST0001")
        self.assertEqual(workflow.agent_input.fraud_type_score_result_id, 7)
        self.assertEqual(workflow.agent_input.risk_score, 84)
        self.assertEqual(workflow.agent_input.risk_grade, RiskGrade.VERY_HIGH)

    def test_public_response_excludes_internal_metrics_and_timestamps(self) -> None:
        payload = _public_response(_agent_response()).model_dump(mode="json")

        self.assertNotIn("generation_metadata", payload)
        self.assertNotIn("created_at", payload)
        self.assertNotIn("completed_at", payload)

    def test_case_lookup_returns_not_found(self) -> None:
        with self.assertRaises(HTTPException) as context:
            get_agent_case("CASE-NOT-FOUND", FakeCaseService(missing=True))

        self.assertEqual(context.exception.status_code, 404)

    def test_transaction_case_lookup_returns_case(self) -> None:
        response = get_agent_case_by_transaction(
            1,
            FakeCaseService(),
        )

        self.assertEqual(response.transaction_id, 1)


if __name__ == "__main__":
    unittest.main()
