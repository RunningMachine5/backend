import unittest

from app.dto.ml_features import MLTransactionFeatures
from app.services.rules.feature_builder import (
    RULE_CONTEXT_FIELDS,
    RULE_EVALUATION_FIELDS,
    RULE_RAW_FEATURES,
    RULE_REGISTRY_EXCLUDED_RAW_FEATURES,
    TRANSITION_LEGACY_DERIVED_FEATURES,
    TRANSITION_LEGACY_RAW_ALIASES,
    RuleFeatureBuilder,
)


def valid_rule_raw_data() -> dict[str, object]:
    """공통 신호가 모두 꺼진 ML raw60의 59개 Feature를 반환한다."""

    return {
        "customer_birth_date": "1986-08-08T00:00:00+09:00",
        "customer_gender": "female",
        "customer_name": "테스트 고객",
        "customer_registration_datetime": "2020-01-01T09:00:00+09:00",
        "customer_credit_rating": 5,
        "customer_flag_change_of_authentication_1": 0,
        "customer_flag_change_of_authentication_2": 0,
        "customer_flag_change_of_authentication_3": 0,
        "customer_flag_change_of_authentication_4": 0,
        "customer_rooting_jailbreak_indicator": 0,
        "customer_mobile_roaming_indicator": 0,
        "customer_vpn_indicator": 0,
        "customer_loan_type": "a",
        "customer_flag_terminal_malicious_behavior_1": 0,
        "customer_flag_terminal_malicious_behavior_2": 0,
        "customer_flag_terminal_malicious_behavior_3": 0,
        "customer_flag_terminal_malicious_behavior_5": 0,
        "customer_flag_terminal_malicious_behavior_6": 0,
        "customer_inquery_atm_limit": 0,
        "customer_increase_atm_limit": 0,
        "account_account_number": "source-001",
        "account_account_type": "a",
        "account_creation_datetime": "2021-01-01T09:00:00+09:00",
        "account_initial_balance": 10_000_000,
        "account_balance": 9_900_000,
        "account_indicator_release_limit_excess": 0,
        "account_amount_daily_limit": 10_000_000,
        "account_indicator_openbanking": 0,
        "account_remaining_amount_daily_limit_exceeded": 9_900_000,
        "account_release_suspention": 0,
        "account_one_month_max_amount": 2_000_000,
        "account_one_month_std_dev": 100_000.0,
        "account_dawn_one_month_max_amount": 1_000_000,
        "account_dawn_one_month_std_dev": 50_000.0,
        "transaction_datetime": "2026-08-07T14:30:00+09:00",
        "transaction_amount": 100_000,
        "channel": "internet",
        "operating_system": "windows",
        "error_code": "a",
        "type_general_automatic": "automatic",
        "ip_address": "192.0.2.1",
        "mac_address": "00:11:22:33:44:55",
        "access_medium": "a",
        "location": "37.5665,126.9780",
        "recipient_account_number": "recipient-001",
        "transaction_num_connection_failure": 0,
        "another_person_account": 0,
        "distance": 0.0,
        "time_difference": "0 days 03:00:00",
        "unused_terminal_status": 0,
        "last_atm_transaction_datetime": None,
        "last_bank_branch_transaction_datetime": None,
        "flag_deposit_more_than_ten_million": 0,
        "unused_account_status": 0,
        "recipient_account_suspend_status": 0,
        "number_of_transaction_with_the_account": 2,
        "transaction_history_with_the_account": 2,
        "first_time_ios_by_vulnerable_user": 0,
        "transaction_resumed_date": None,
    }


def valid_rule_features(
    raw_data: dict[str, object] | None = None,
) -> MLTransactionFeatures:
    return MLTransactionFeatures.model_validate(raw_data or valid_rule_raw_data())


class RuleFeatureBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = RuleFeatureBuilder()

    def build(self, raw_data: dict[str, object]) -> dict[str, object]:
        return self.builder.build(valid_rule_features(raw_data))

    def test_raw_contract_has_exactly_59_model_inputs(self) -> None:
        raw_data = valid_rule_raw_data()

        self.assertEqual(len(raw_data), 59)
        self.assertEqual(set(raw_data), set(RULE_RAW_FEATURES))

    def test_accepts_shared_ml_transaction_features(self) -> None:
        context = self.builder.build(valid_rule_features())

        self.assertEqual(context["transaction_amount"], 100_000.0)

    def test_builds_final_common_derived_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_birth_date": "1960-01-01T00:00:00+09:00",
                "customer_loan_type": "b",
                "customer_inquery_atm_limit": 1,
                "customer_increase_atm_limit": 1,
                "account_indicator_release_limit_excess": 1,
                "customer_flag_terminal_malicious_behavior_1": 1,
                "customer_flag_terminal_malicious_behavior_2": 1,
                "customer_flag_terminal_malicious_behavior_3": 1,
                "customer_flag_terminal_malicious_behavior_5": 1,
                "customer_flag_terminal_malicious_behavior_6": 1,
                "customer_flag_change_of_authentication_1": 1,
                "customer_flag_change_of_authentication_2": 1,
                "customer_flag_change_of_authentication_3": 1,
                "customer_vpn_indicator": 1,
                "channel": "mobile",
                "operating_system": "android",
                "transaction_amount": 9_000_000,
                "account_balance": 1_000_000,
                "account_remaining_amount_daily_limit_exceeded": 500_000,
                "account_release_suspention": 1,
                "recipient_account_suspend_status": 1,
                "unused_account_status": 1,
                "transaction_resumed_date": "2026-07-20T14:30:00+09:00",
                "another_person_account": 1,
                "transaction_history_with_the_account": 1,
                "number_of_transaction_with_the_account": 3,
                "distance": 150.0,
                "time_difference": "0 days 01:00:00",
            }
        )

        context = self.build(raw_data)

        self.assertEqual(set(context), set(RULE_EVALUATION_FIELDS))
        self.assertEqual(context["transaction_age"], 66)
        self.assertEqual(context["authentication_change_count"], 3)
        self.assertTrue(context["strong_auth_change"])
        self.assertTrue(context["loan_related"])
        self.assertEqual(context["limit_action_count"], 3)
        self.assertTrue(context["all_limit_actions"])
        self.assertEqual(context["device_compromise_count"], 3)
        self.assertTrue(context["device_compromise_2plus"])
        self.assertTrue(context["recipient_transfer"])
        self.assertTrue(context["rapid_repeat"])
        self.assertTrue(context["amount_anomaly"])
        self.assertTrue(context["balance_depletion"])
        self.assertTrue(context["daily_limit_pressure"])
        self.assertTrue(context["severe_amount_context"])
        self.assertTrue(context["loan_escalation_context"])
        self.assertTrue(context["impossible_travel"])
        self.assertTrue(context["recently_resumed"])
        self.assertTrue(context["vulnerable_mobile"])
        self.assertTrue(context["suspension_pair"])
        self.assertTrue(context["vpn_or_roaming"])

    def test_sensitive_identifiers_are_not_in_rule_context(self) -> None:
        context = self.build(valid_rule_raw_data())

        self.assertTrue(RULE_REGISTRY_EXCLUDED_RAW_FEATURES.isdisjoint(context))
        self.assertNotIn("customer_birth_date", RULE_CONTEXT_FIELDS)
        self.assertIn("transaction_age", RULE_CONTEXT_FIELDS)

    def test_transition_aliases_only_exist_in_engine_evaluation_context(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["channel"] = "ATM"
        raw_data["operating_system"] = "iOS"

        context = self.build(raw_data)

        self.assertEqual(context["Channel"], "ATM")
        self.assertEqual(context["Operating_System"], "iOS")
        self.assertEqual(context["Customer_Birthyear"], 1986)
        self.assertEqual(context["Account_release_suspension"], 0)
        self.assertTrue(
            set(TRANSITION_LEGACY_RAW_ALIASES).isdisjoint(RULE_CONTEXT_FIELDS)
        )
        self.assertTrue(
            TRANSITION_LEGACY_DERIVED_FEATURES.isdisjoint(RULE_CONTEXT_FIELDS)
        )

    def test_age_matches_ml_full_birth_date_semantics(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["customer_birth_date"] = "1966-08-08T00:00:00+09:00"

        self.assertEqual(self.build(raw_data)["transaction_age"], 59)

        raw_data["transaction_datetime"] = "2026-08-08T00:00:00+09:00"
        self.assertEqual(self.build(raw_data)["transaction_age"], 60)

    def test_birth_date_without_timezone_supports_aware_transaction_time(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["customer_birth_date"] = "1986-08-08T00:00:00"

        context = self.build(raw_data)

        self.assertEqual(context["transaction_age"], 39)

    def test_severe_amount_requires_anomaly_and_additional_pressure(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["transaction_amount"] = 9_000_000
        raw_data["account_one_month_max_amount"] = 10_000_000

        context = self.build(raw_data)

        self.assertTrue(context["balance_depletion"])
        self.assertTrue(context["daily_limit_pressure"])
        self.assertFalse(context["amount_anomaly"])
        self.assertFalse(context["severe_amount_context"])

    def test_nullable_ml_owner_fields_do_not_break_rule_context(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "account_account_type": "e",
                "account_initial_balance": None,
                "account_balance": None,
                "account_remaining_amount_daily_limit_exceeded": None,
                "access_medium": None,
            }
        )

        context = self.build(raw_data)

        self.assertEqual(context["account_account_type"], "e")
        self.assertIsNone(context["account_initial_balance"])
        self.assertIsNone(context["account_balance"])
        self.assertIsNone(
            context["account_remaining_amount_daily_limit_exceeded"]
        )
        self.assertIsNone(context["access_medium"])
        self.assertFalse(context["balance_depletion"])
        self.assertFalse(context["daily_limit_pressure"])

    def test_empty_error_code_is_preserved_for_rules(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["error_code"] = ""

        context = self.build(raw_data)

        self.assertEqual(context["error_code"], "")

    def test_impossible_travel_uses_distance_and_two_hour_boundary(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["distance"] = 100
        raw_data["time_difference"] = 7_200
        self.assertTrue(self.build(raw_data)["impossible_travel"])

        raw_data["time_difference"] = 0
        self.assertFalse(self.build(raw_data)["impossible_travel"])
        raw_data["time_difference"] = "0 days 02:00:01"
        self.assertFalse(self.build(raw_data)["impossible_travel"])

    def test_amount_anomaly_uses_strict_monthly_baseline_comparison(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["account_initial_balance"] = 10_000
        raw_data["account_amount_daily_limit"] = 10_000
        raw_data["account_one_month_max_amount"] = 300
        raw_data["account_one_month_std_dev"] = 100
        raw_data["transaction_amount"] = 300
        self.assertFalse(self.build(raw_data)["amount_anomaly"])

        raw_data["transaction_amount"] = 301
        self.assertTrue(self.build(raw_data)["amount_anomaly"])

    def test_rule_amount_policy_uses_absolute_transaction_amount(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["transaction_amount"] = 0
        self.assertEqual(self.build(raw_data)["transaction_amount"], 0)

        raw_data = valid_rule_raw_data()
        raw_data["transaction_amount"] = -100_000
        self.assertEqual(self.build(raw_data)["transaction_amount"], 100_000)

        raw_data = valid_rule_raw_data()
        raw_data["account_balance"] = -1
        self.assertEqual(self.build(raw_data)["account_balance"], -1.0)


if __name__ == "__main__":
    unittest.main()
