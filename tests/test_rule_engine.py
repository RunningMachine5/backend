import unittest
from unittest.mock import patch

from app.services.rules.defaults import DEFAULT_RULE_SET
from app.services.rules.engine import (
    FraudRuleDefinition,
    RuleComponentDefinition,
    RuleEngine,
    RuleSetDefinition,
    RuleSetValidationError,
)
from tests.test_rule_feature_builder import valid_rule_features, valid_rule_raw_data

FINAL_TYPE_CODES = {
    "VOICE_PHISHING",
    "MESSENGER_PHISHING",
    "ACCOUNT_TAKEOVER",
    "FRAUD_USED_ACCOUNT",
}


class RuleEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RuleEngine()

    def score(
        self,
        raw_data: dict[str, object],
        rule_set: RuleSetDefinition | None = None,
    ):
        return self.engine.score(valid_rule_features(raw_data), rule_set)

    def test_default_rule_set_contains_final_four_fraud_types(self) -> None:
        self.engine.validate_rule_set(DEFAULT_RULE_SET)

        self.assertEqual(len(DEFAULT_RULE_SET.rules), 4)
        self.assertEqual(
            {rule.type_code for rule in DEFAULT_RULE_SET.rules},
            FINAL_TYPE_CODES,
        )

        expected_weights = {
            "VOICE_PHISHING": {
                "phone_number_manipulation": 0.30,
                "loan_escalation_context": 0.25,
                "all_limit_actions": 0.15,
                "severe_amount_context": 0.15,
                "recipient_transfer_with_severe_amount": 0.10,
                "remote_control": 0.05,
            },
            "MESSENGER_PHISHING": {
                "remote_control": 0.30,
                "open_banking_with_rapid_repeat": 0.20,
                "strong_auth_change_with_remote_control": 0.20,
                "vulnerable_mobile_recipient_transfer": 0.15,
                "recipient_transfer_with_remote_control": 0.10,
                "rapid_repeat": 0.05,
            },
            "ACCOUNT_TAKEOVER": {
                "unused_terminal_with_device_compromise": 0.25,
                "device_compromise_2plus": 0.20,
                "remote_control": 0.15,
                "strong_auth_change_with_compromise": 0.15,
                "impossible_travel": 0.15,
                "vpn_or_roaming_with_impossible_travel": 0.05,
                "connection_failures": 0.05,
            },
            "FRAUD_USED_ACCOUNT": {
                "both_accounts_restricted": 0.45,
                "suspension_release_only_with_context": 0.15,
                "recipient_suspended_only_with_context": 0.15,
                "recently_resumed_with_large_deposit": 0.10,
                "large_deposit_with_rapid_repeat": 0.10,
                "rapid_repeat": 0.05,
            },
        }
        actual_weights = {
            rule.type_code: {
                component.component_key: component.weight
                for component in rule.components
            }
            for rule in DEFAULT_RULE_SET.rules
        }
        self.assertEqual(actual_weights, expected_weights)

    def test_validates_each_expression_once_before_scoring(self) -> None:
        component_count = sum(
            len(rule.components) for rule in DEFAULT_RULE_SET.rules if rule.enabled
        )
        evaluator = self.engine.expression_evaluator

        with (
            patch.object(
                evaluator,
                "validate",
                wraps=evaluator.validate,
            ) as validate,
            patch.object(
                evaluator,
                "evaluate_validated",
                wraps=evaluator.evaluate_validated,
            ) as evaluate_validated,
        ):
            result = self.engine.score(valid_rule_features(), DEFAULT_RULE_SET)

        self.assertEqual(validate.call_count, component_count)
        self.assertEqual(evaluate_validated.call_count, component_count)
        self.assertEqual(set(result.type_scores), FINAL_TYPE_CODES)

    def test_validated_scoring_returns_the_same_result(self) -> None:
        features = valid_rule_features()

        regular = self.engine.score(features, DEFAULT_RULE_SET)
        self.engine.validate_rule_set(DEFAULT_RULE_SET)
        validated = self.engine.score_validated(features, DEFAULT_RULE_SET)

        self.assertEqual(validated, regular)

    def test_voice_phishing_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_flag_terminal_malicious_behavior_1": 1,
                "customer_loan_type": "b",
                "customer_inquery_atm_limit": 1,
                "customer_increase_atm_limit": 1,
                "account_indicator_release_limit_excess": 1,
                "transaction_amount": 9_000_000,
                "transaction_history_with_the_account": 1,
                "another_person_account": 1,
                "customer_flag_terminal_malicious_behavior_2": 1,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["VOICE_PHISHING"], 1.0)
        self.assertEqual(
            set(result.matched_components["VOICE_PHISHING"]),
            {
                "phone_number_manipulation",
                "loan_escalation_context",
                "all_limit_actions",
                "severe_amount_context",
                "recipient_transfer_with_severe_amount",
                "remote_control",
            },
        )
        self.assertEqual(set(result.type_scores), FINAL_TYPE_CODES)

    def test_voice_phishing_rejects_former_single_signal_scores(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_loan_type": "b",
                "customer_inquery_atm_limit": 1,
                "transaction_amount": 301,
                "account_one_month_max_amount": 300,
                "account_one_month_std_dev": 100,
                "transaction_history_with_the_account": 1,
                "another_person_account": 1,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["VOICE_PHISHING"], 0.0)
        self.assertEqual(result.matched_components["VOICE_PHISHING"], [])

    def test_messenger_phishing_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_birth_date": "1960-01-01T00:00:00+09:00",
                "channel": "mobile",
                "operating_system": "android",
                "customer_flag_terminal_malicious_behavior_2": 1,
                "account_indicator_openbanking": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
                "transaction_history_with_the_account": 1,
                "another_person_account": 1,
                "number_of_transaction_with_the_account": 3,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["MESSENGER_PHISHING"], 1.0)
        self.assertEqual(len(result.matched_components["MESSENGER_PHISHING"]), 6)

    def test_messenger_phishing_requires_combined_context(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_birth_date": "1960-01-01T00:00:00+09:00",
                "channel": "mobile",
                "account_indicator_openbanking": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["MESSENGER_PHISHING"], 0.0)
        self.assertEqual(result.matched_components["MESSENGER_PHISHING"], [])

    def test_account_takeover_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "unused_terminal_status": 1,
                "customer_flag_terminal_malicious_behavior_3": 1,
                "customer_flag_terminal_malicious_behavior_5": 1,
                "customer_flag_terminal_malicious_behavior_2": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
                "distance": 100,
                "time_difference": "0 days 02:00:00",
                "customer_vpn_indicator": 1,
                "transaction_num_connection_failure": 3,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["ACCOUNT_TAKEOVER"], 1.0)
        self.assertEqual(len(result.matched_components["ACCOUNT_TAKEOVER"]), 7)

    def test_account_takeover_requires_compromise_and_travel_context(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "unused_terminal_status": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
                "customer_vpn_indicator": 1,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["ACCOUNT_TAKEOVER"], 0.0)
        self.assertEqual(result.matched_components["ACCOUNT_TAKEOVER"], [])

    def test_fraud_used_account_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "recipient_release_suspension": 1,
                "recipient_account_suspend_status": 1,
                "unused_account_status": 1,
                "recipient_transaction_resumed_date": "2026-07-20T14:30:00+09:00",
                "flag_deposit_more_than_ten_million": 1,
                "number_of_transaction_with_the_account": 3,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["FRAUD_USED_ACCOUNT"], 0.70)
        self.assertEqual(
            set(result.matched_components["FRAUD_USED_ACCOUNT"]),
            {
                "both_accounts_restricted",
                "recently_resumed_with_large_deposit",
                "large_deposit_with_rapid_repeat",
                "rapid_repeat",
            },
        )

    def test_returns_every_type_even_when_all_scores_are_zero(self) -> None:
        result = self.score(valid_rule_raw_data())

        self.assertEqual(set(result.type_scores), FINAL_TYPE_CODES)
        self.assertTrue(all(score == 0.0 for score in result.type_scores.values()))

    def test_preserves_close_scores_without_selecting_one_type(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_flag_terminal_malicious_behavior_2": 1,
                "customer_flag_terminal_malicious_behavior_3": 1,
                "customer_flag_terminal_malicious_behavior_5": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
            }
        )

        result = self.score(raw_data)

        self.assertEqual(result.type_scores["MESSENGER_PHISHING"], 0.5)
        self.assertEqual(result.type_scores["ACCOUNT_TAKEOVER"], 0.5)

    def test_new_fraud_type_is_evaluated_without_enum_or_engine_change(self) -> None:
        new_rule = FraudRuleDefinition(
            type_code="NEW_FRAUD_TYPE",
            display_name="신규 사기유형",
            components=(
                RuleComponentDefinition(
                    component_key="open_banking_signal",
                    name="오픈뱅킹 신호",
                    condition_expression={
                        "field": "account_indicator_openbanking",
                        "operator": "EQ",
                        "value": 1,
                    },
                    weight=1.0,
                ),
            ),
        )
        custom_rule_set = RuleSetDefinition(
            version="v2",
            rules=(*DEFAULT_RULE_SET.rules, new_rule),
        )
        raw_data = valid_rule_raw_data()
        raw_data["account_indicator_openbanking"] = 1

        result = self.score(raw_data, custom_rule_set)

        self.assertEqual(result.type_scores["NEW_FRAUD_TYPE"], 1.0)

    def test_rejects_rule_when_component_weights_do_not_sum_to_one(self) -> None:
        invalid_rule = FraudRuleDefinition(
            type_code="INVALID_RULE",
            display_name="잘못된 룰",
            components=(
                RuleComponentDefinition(
                    component_key="only_component",
                    name="단일 구성요소",
                    condition_expression={
                        "field": "loan_related",
                        "operator": "EQ",
                        "value": True,
                    },
                    weight=0.5,
                ),
            ),
        )
        invalid_rule_set = RuleSetDefinition(
            version="invalid",
            rules=(DEFAULT_RULE_SET.rules[0], invalid_rule),
        )

        with self.assertRaisesRegex(RuleSetValidationError, "합계는 1.0"):
            self.engine.validate_rule_set(invalid_rule_set)


if __name__ == "__main__":
    unittest.main()
