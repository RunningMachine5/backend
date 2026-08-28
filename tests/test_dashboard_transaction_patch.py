import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.enums import RiskGrade
from app.dto.agent import AgentInputDTO
from app.dto.fraud_detection import FraudDetectionResponseDTO
from app.services.dashboard.transaction_patch import (
    build_transaction_dashboard_event,
)


class DashboardTransactionPatchTest(unittest.TestCase):
    def test_builds_json_safe_patch_for_immediate_dashboard_upsert(self) -> None:
        transaction_datetime = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
        response = FraudDetectionResponseDTO(
            transaction_id=42,
            prediction_status="DECLINED",
            predict_result=True,
            predict_proba=0.91,
            rule_set_id=1,
            rule_scores={"ACCOUNT_TAKEOVER": 0.8},
            transaction_amount=500_000,
            transaction_datetime=transaction_datetime,
            risk_score=91,
            created_at=transaction_datetime,
            received_at=transaction_datetime,
        )
        result = SimpleNamespace(
            response=response,
            transaction=SimpleNamespace(
                transaction_amount=500_000,
                transaction_datetime=transaction_datetime,
                channel="mobile",
                ip_address="203.0.113.10",
            ),
            score_result=SimpleNamespace(
                primary_fraud_type="ACCOUNT_TAKEOVER",
                rule_filter_status="APPLIED",
            ),
        )
        agent_input = AgentInputDTO(
            transaction_id=42,
            fraud_type_score_result_id=7,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
        )

        event = build_transaction_dashboard_event(
            source="transaction",
            result=result,
            agent_input=agent_input,
        )

        json.dumps(event)
        patch_payload = event["transaction_patch"]
        self.assertEqual(event["source"], "transaction")
        self.assertEqual(patch_payload["event_id"], "transaction:42")
        self.assertEqual(patch_payload["transaction"]["transaction_id"], 42)
        self.assertEqual(patch_payload["channel"], "mobile")
        self.assertEqual(patch_payload["date_label"], "8/23")
        self.assertTrue(patch_payload["rule_analysis_completed"])
        self.assertTrue(patch_payload["overview_suspicious"])
        self.assertEqual(
            patch_payload["suspicious_case"]["execution_status"],
            "PROCESSING",
        )
        self.assertEqual(
            patch_payload["suspicious_case"]["risk_grade"],
            "VERY_HIGH",
        )

    def test_ml_fraud_without_rule_is_still_available_as_risk_row(self) -> None:
        transaction_datetime = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
        response = FraudDetectionResponseDTO(
            transaction_id=43,
            prediction_status="DECLINED",
            predict_result=True,
            predict_proba=0.9,
            transaction_amount=300_000,
            transaction_datetime=transaction_datetime,
            risk_score=90,
            created_at=transaction_datetime,
            received_at=transaction_datetime,
        )
        result = SimpleNamespace(
            response=response,
            transaction=SimpleNamespace(
                transaction_amount=300_000,
                transaction_datetime=transaction_datetime,
                channel="internet",
                ip_address=None,
            ),
            score_result=None,
        )

        event = build_transaction_dashboard_event(
            source="transaction",
            result=result,
            agent_input=None,
        )

        patch_payload = event["transaction_patch"]
        self.assertFalse(patch_payload["rule_analysis_completed"])
        self.assertFalse(patch_payload["overview_suspicious"])
        self.assertEqual(
            patch_payload["suspicious_case"]["execution_status"],
            "NOT_AVAILABLE",
        )
        self.assertIsNone(patch_payload["suspicious_case"]["risk_score"])
        self.assertIsNone(patch_payload["suspicious_case"]["risk_grade"])

    def test_normal_transaction_has_no_suspicious_case(self) -> None:
        transaction_datetime = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
        response = FraudDetectionResponseDTO(
            transaction_id=44,
            prediction_status="COMPLETED",
            predict_result=False,
            predict_proba=0.1,
            transaction_amount=10_000,
            transaction_datetime=transaction_datetime,
            risk_score=10,
            created_at=transaction_datetime,
            received_at=transaction_datetime,
        )
        result = SimpleNamespace(
            response=response,
            transaction=SimpleNamespace(
                transaction_datetime=transaction_datetime,
                channel="atm",
            ),
            score_result=None,
        )

        event = build_transaction_dashboard_event(
            source="demo_transaction",
            result=result,
            agent_input=None,
        )

        self.assertFalse(event["transaction_patch"]["overview_suspicious"])
        self.assertIsNone(event["transaction_patch"]["suspicious_case"])


if __name__ == "__main__":
    unittest.main()
