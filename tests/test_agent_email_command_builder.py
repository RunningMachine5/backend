import unittest

from app.domain.agent_status import (
    ClassificationStatus,
    RuleFilterStatus,
)
from app.dto.agent import FraudTypeScoreResultDTO
from app.services.agent.email_command_builder import (
    build_fraud_alert_email_command,
)
from app.services.agent.type_confidence import calculate_type_confidence


class AgentEmailCommandBuilderTest(unittest.TestCase):
    """유형 확실성별 이메일 표시 유형 선택 규칙을 검증한다."""

    def test_confident_case_uses_rule_top_two_types(self) -> None:
        command = build_fraud_alert_email_command(
            transaction_id=1,
            type_confidence=self._confidence(
                account_takeover=0.80,
                messenger_phishing=0.40,
            ),
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.primary_suspected_score, 0.80)
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(command.secondary_suspected_score, 0.40)
        self.assertEqual(
            command.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_ambiguous_case_keeps_rule_top_two_order(self) -> None:
        command = build_fraud_alert_email_command(
            transaction_id=2,
            type_confidence=self._confidence(
                account_takeover=0.62,
                messenger_phishing=0.57,
            ),
        )

        self.assertEqual(command.primary_suspected_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(command.primary_suspected_score, 0.62)
        self.assertEqual(command.secondary_suspected_type, "MESSENGER_PHISHING")
        self.assertEqual(command.secondary_suspected_score, 0.57)
        self.assertEqual(
            command.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_empty_transaction_id_is_rejected_at_builder_boundary(self) -> None:
        with self.assertRaises(ValueError):
            build_fraud_alert_email_command(
                transaction_id=0,
                type_confidence=self._confidence(
                    account_takeover=0.80,
                    messenger_phishing=0.40,
                ),
            )

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

    @classmethod
    def _confidence(
        cls,
        *,
        account_takeover: float,
        messenger_phishing: float,
    ):
        return calculate_type_confidence(
            cls._rule_result(
                account_takeover=account_takeover,
                messenger_phishing=messenger_phishing,
            ).type_scores
        )

if __name__ == "__main__":
    unittest.main()
