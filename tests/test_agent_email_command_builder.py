import unittest

from app.domain.agent_status import (
    ClassificationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.dto.agent import FraudTypeScoreResultDTO, InvestigationResultDTO
from app.services.agent.email_command_builder import (
    build_fraud_alert_email_command,
)


class AgentEmailCommandBuilderTest(unittest.TestCase):
    """유형 확실성별 이메일 표시 유형 선택 규칙을 검증한다."""

    def test_confident_case_uses_rule_top_two_types(self) -> None:
        command = build_fraud_alert_email_command(
            transaction_id="TX-001",
            rule_result=self._rule_result(
                account_takeover=0.80,
                messenger_phishing=0.40,
            ),
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(
            command.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_ambiguous_case_can_reorder_rule_top_two(self) -> None:
        investigation = self._investigation("MESSENGER_PHISHING")

        command = build_fraud_alert_email_command(
            transaction_id="TX-002",
            rule_result=self._rule_result(
                account_takeover=0.62,
                messenger_phishing=0.57,
            ),
            investigation_result=investigation,
        )

        self.assertEqual(command.primary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(command.secondary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(
            command.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_missing_investigation_falls_back_to_rule_order(self) -> None:
        command = build_fraud_alert_email_command(
            transaction_id="TX-003",
            rule_result=self._rule_result(
                account_takeover=0.62,
                messenger_phishing=0.57,
            ),
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(
            command.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_recommendation_outside_top_two_keeps_rule_order(self) -> None:
        command = build_fraud_alert_email_command(
            transaction_id="TX-004",
            rule_result=self._rule_result(
                account_takeover=0.62,
                messenger_phishing=0.57,
            ),
            investigation_result=self._investigation("VOICE_PHISHING"),
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")

    def test_empty_transaction_id_is_rejected_at_builder_boundary(self) -> None:
        with self.assertRaises(ValueError):
            build_fraud_alert_email_command(
                transaction_id=" ",
                rule_result=self._rule_result(
                    account_takeover=0.80,
                    messenger_phishing=0.40,
                ),
            )

    def test_failed_investigation_ignores_stale_recommendation(self) -> None:
        investigation = self._investigation(
            "MESSENGER_PHISHING",
            status=InvestigationStatus.FAILED,
        )

        command = build_fraud_alert_email_command(
            transaction_id="TX-005",
            rule_result=self._rule_result(
                account_takeover=0.62,
                messenger_phishing=0.57,
            ),
            investigation_result=investigation,
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")

    @staticmethod
    def _rule_result(
        *,
        account_takeover: float,
        messenger_phishing: float,
    ) -> FraudTypeScoreResultDTO:
        return FraudTypeScoreResultDTO(
            fraud_type_score_result_id=1,
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

    @staticmethod
    def _investigation(
        recommended_type: str,
        *,
        status: InvestigationStatus = InvestigationStatus.COMPLETED,
    ) -> InvestigationResultDTO:
        return InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=0.05,
            investigation_status=status,
            recommended_fraud_type=recommended_type,
            recommendation_reason="유사 완료 사건 근거가 확인되었다.",
            best_similarity_score=0.90,
            common_evidence_codes=["remote_control"],
            confirmed_case_count=2,
        )


if __name__ == "__main__":
    unittest.main()
