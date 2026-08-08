import unittest

from app.services.rules.defaults import DEFAULT_RULE_SET
from app.services.rules.engine import (
    FraudRuleDefinition,
    RuleComponentDefinition,
    RuleEngine,
    RuleSetDefinition,
    RuleSetValidationError,
)
from tests.test_rule_feature_builder import valid_rule_raw_data


FINAL_TYPE_CODES = {
    "VOICE_PHISHING",
    "MESSENGER_PHISHING",
    "ACCOUNT_TAKEOVER",
    "FRAUD_USED_ACCOUNT",
    "CARD_FRAUD",
}


class RuleEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RuleEngine()

    def test_default_rule_set_contains_final_five_fraud_types(self) -> None:
        self.engine.validate_rule_set(DEFAULT_RULE_SET)

        self.assertEqual(len(DEFAULT_RULE_SET.rules), 5)
        self.assertEqual(
            {rule.type_code for rule in DEFAULT_RULE_SET.rules},
            FINAL_TYPE_CODES,
        )

    def test_voice_phishing_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Customer_flag_terminal_malicious_behavior_1": 1,
                "Customer_loan_type": "b",
                "Customer_inquery_atm_limit": 1,
                "Transaction_Amount": 9_000_000,
                "Transaction_history_with_the_account": 1,
                "Another_Person_Account": 1,
                "Customer_flag_terminal_malicious_behavior_2": 1,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.status, "CLASSIFIED")
        self.assertEqual(result.fraud_type, "VOICE_PHISHING")
        self.assertEqual(result.top_score, 1.0)
        self.assertEqual(
            set(result.matched_components["VOICE_PHISHING"]),
            {
                "phone_number_manipulation",
                "loan_related",
                "limit_adjustment_detected",
                "high_value_or_balance_pressure",
                "new_or_rare_recipient",
                "another_person_account",
                "remote_control",
            },
        )
        self.assertEqual(set(result.type_scores), FINAL_TYPE_CODES)

    def test_messenger_phishing_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Customer_Birthyear": 1960,
                "Channel": "mobile",
                "Operating_System": "Android",
                "Customer_flag_terminal_malicious_behavior_2": 1,
                "Account_indicator_Openbanking": 1,
                "Customer_flag_change_of_authentication_1": 1,
                "Transaction_history_with_the_account": 1,
                "Another_Person_Account": 1,
                "Number_of_transaction_with_the_account": 3,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.fraud_type, "MESSENGER_PHISHING")
        self.assertEqual(result.top_score, 1.0)
        self.assertEqual(len(result.matched_components["MESSENGER_PHISHING"]), 6)

    def test_account_takeover_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Unused_terminal_status": 1,
                "Customer_flag_terminal_malicious_behavior_3": 1,
                "Customer_flag_terminal_malicious_behavior_5": 1,
                "Customer_flag_terminal_malicious_behavior_2": 1,
                "Customer_flag_change_of_authentication_2": 1,
                "Distance": 100,
                "Time Difference": "0 days 02:00:00",
                "Customer_VPN_Indicator": 1,
                "Transaction_num_connection_failure": 3,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.fraud_type, "ACCOUNT_TAKEOVER")
        self.assertEqual(result.top_score, 1.0)
        self.assertEqual(len(result.matched_components["ACCOUNT_TAKEOVER"]), 7)

    def test_fraud_used_account_uses_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Account_release_suspension": 1,
                "Recipient_account_suspend_status": 1,
                "Unused_account_status": 1,
                "Transaction_resumed_date": "2026-07-20T14:30:00+09:00",
                "Flag_deposit_more_than_tenMillion": 1,
                "Number_of_transaction_with_the_account": 3,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.fraud_type, "FRAUD_USED_ACCOUNT")
        self.assertEqual(result.top_score, 1.0)
        self.assertEqual(len(result.matched_components["FRAUD_USED_ACCOUNT"]), 6)

    def test_card_fraud_uses_proxy_gate_and_final_weighted_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Channel": "ATM",
                "Another_Person_Account": 0,
                "Customer_loan_type": "a",
                "Unused_terminal_status": 1,
                "Distance": 100,
                "Time Difference": "0 days 02:00:00",
                "Number_of_transaction_with_the_account": 3,
                "Transaction_history_with_the_account": 1,
                "Type_General_Automatic": "general",
                "Customer_mobile_roaming_indicator": 1,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.fraud_type, "CARD_FRAUD")
        self.assertEqual(result.top_score, 1.0)
        self.assertEqual(len(result.matched_components["CARD_FRAUD"]), 7)

        raw_data["Channel"] = "internet"
        gated = self.engine.classify(raw_data)
        self.assertEqual(gated.type_scores["CARD_FRAUD"], 0.0)
        self.assertEqual(gated.matched_components["CARD_FRAUD"], [])

    def test_returns_unclassified_below_minimum_score(self) -> None:
        result = self.engine.classify(valid_rule_raw_data())

        self.assertEqual(result.status, "UNCLASSIFIED")
        self.assertIsNone(result.fraud_type)
        self.assertEqual(result.top_score, 0.0)
        self.assertEqual(set(result.type_scores), FINAL_TYPE_CODES)
        self.assertTrue(all(score == 0.0 for score in result.type_scores.values()))
        self.assertEqual(result.decision_reason, "BELOW_MINIMUM_SCORE")

    def test_returns_unclassified_when_top_scores_are_ambiguous(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Customer_flag_terminal_malicious_behavior_2": 1,
                "Customer_flag_change_of_authentication_1": 1,
                "Number_of_transaction_with_the_account": 3,
                "Unused_terminal_status": 1,
            }
        )

        result = self.engine.classify(raw_data)

        self.assertEqual(result.status, "UNCLASSIFIED")
        self.assertIsNone(result.fraud_type)
        self.assertEqual(result.top_score, 0.5)
        self.assertEqual(result.second_score, 0.5)
        self.assertEqual(result.score_gap, 0.0)
        self.assertEqual(result.decision_reason, "AMBIGUOUS_TOP_SCORES")

    def test_new_fraud_type_is_evaluated_without_enum_or_engine_change(self) -> None:
        new_rule = FraudRuleDefinition(
            type_code="NEW_FRAUD_TYPE",
            display_name="신규 사기유형",
            components=(
                RuleComponentDefinition(
                    component_key="open_banking_signal",
                    name="오픈뱅킹 신호",
                    condition_expression={
                        "field": "Account_indicator_Openbanking",
                        "operator": "EQ",
                        "value": 1,
                    },
                    weight=1.0,
                ),
            ),
        )
        custom_rule_set = RuleSetDefinition(
            version="v2",
            minimum_score=0.5,
            ambiguity_margin=0.1,
            rules=(*DEFAULT_RULE_SET.rules, new_rule),
        )
        raw_data = valid_rule_raw_data()
        raw_data["Account_indicator_Openbanking"] = 1

        result = self.engine.classify(raw_data, custom_rule_set)

        self.assertEqual(result.status, "CLASSIFIED")
        self.assertEqual(result.fraud_type, "NEW_FRAUD_TYPE")
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
