import unittest

from app.services.rules.feature_builder import (
    ACCOUNT_RELEASE_FIELD,
    LEGACY_ACCOUNT_RELEASE_FIELD,
    RULE_CONTEXT_FIELDS,
    RULE_EVALUATION_FIELDS,
    RuleFeatureBuilder,
    RuleFeatureError,
)
from tests.ml_feature_fixture import valid_ml_raw_data


def valid_rule_raw_data() -> dict[str, object]:
    """최종 룰의 공통 신호가 모두 꺼진 54개 원본 거래를 반환한다."""

    raw_data = valid_ml_raw_data()
    release_value = raw_data.pop(LEGACY_ACCOUNT_RELEASE_FIELD)
    raw_data.update(
        {
            "Customer_Birthyear": 1986,
            "Transaction_Datetime": "2026-08-07T14:30:00+09:00",
            "Customer_loan_type": "a",
            "Customer_inquery_atm_limit": 0,
            "Customer_increase_atm_limit": 0,
            "Customer_flag_terminal_malicious_behavior_1": 0,
            "Customer_flag_terminal_malicious_behavior_2": 0,
            "Customer_flag_terminal_malicious_behavior_3": 0,
            "Customer_flag_terminal_malicious_behavior_5": 0,
            "Customer_flag_terminal_malicious_behavior_6": 0,
            "Customer_rooting_jailbreak_indicator": 0,
            "Customer_VPN_Indicator": 0,
            "Customer_mobile_roaming_indicator": 0,
            "Customer_flag_change_of_authentication_1": 0,
            "Customer_flag_change_of_authentication_2": 0,
            "Customer_flag_change_of_authentication_3": 0,
            "Customer_flag_change_of_authentication_4": 0,
            "Channel": "internet",
            "Operating_System": "Windows",
            "Access_Medium": "a",
            "Transaction_num_connection_failure": 0,
            "Transaction_Amount": 100_000,
            "Account_initial_balance": 10_000_000,
            "Account_balance": 9_900_000,
            "Account_indicator_release_limit_excess": 0,
            "Account_amount_daily_limit": 10_000_000,
            "Account_remaining_amount_daily_limit_exceeded": 9_900_000,
            "Account_one_month_max_amount": 2_000_000,
            "Account_one_month_std_dev": 100_000.0,
            ACCOUNT_RELEASE_FIELD: release_value,
            "Recipient_account_suspend_status": 0,
            "Unused_account_status": 0,
            "Transaction_resumed_date": None,
            "Another_Person_Account": 0,
            "Transaction_history_with_the_account": 2,
            "Number_of_transaction_with_the_account": 2,
            "Flag_deposit_more_than_tenMillion": 0,
            "Distance": 0.0,
            "Time Difference": "0 days 03:00:00",
            "Unused_terminal_status": 0,
            "Type_General_Automatic": "automatic",
            "Account_indicator_Openbanking": 0,
            "First_time_iOS_by_vulnerable_user": 0,
        }
    )
    return raw_data


class RuleFeatureBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = RuleFeatureBuilder()

    def test_builds_final_common_derived_signals(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "Customer_Birthyear": 1960,
                "Customer_loan_type": "b",
                "Customer_inquery_atm_limit": 1,
                "Customer_increase_atm_limit": 1,
                "Account_indicator_release_limit_excess": 1,
                "Customer_flag_terminal_malicious_behavior_1": 1,
                "Customer_flag_terminal_malicious_behavior_2": 1,
                "Customer_flag_terminal_malicious_behavior_3": 1,
                "Customer_flag_terminal_malicious_behavior_5": 1,
                "Customer_flag_terminal_malicious_behavior_6": 1,
                "Customer_flag_change_of_authentication_1": 1,
                "Customer_flag_change_of_authentication_2": 1,
                "Customer_flag_change_of_authentication_3": 1,
                "Customer_VPN_Indicator": 1,
                "Channel": "mobile",
                "Operating_System": "Android",
                "Transaction_Amount": 9_000_000,
                "Account_balance": 1_000_000,
                "Account_remaining_amount_daily_limit_exceeded": 500_000,
                ACCOUNT_RELEASE_FIELD: 1,
                "Recipient_account_suspend_status": 1,
                "Unused_account_status": 1,
                "Transaction_resumed_date": "2026-07-20T14:30:00+09:00",
                "Another_Person_Account": 1,
                "Transaction_history_with_the_account": 1,
                "Number_of_transaction_with_the_account": 3,
                "Distance": 150.0,
                "Time Difference": "0 days 01:00:00",
            }
        )

        context = self.builder.build(raw_data)

        self.assertEqual(set(context), set(RULE_EVALUATION_FIELDS))
        self.assertEqual(context["transaction_age"], 66)
        self.assertEqual(context["authentication_change_count"], 3)
        self.assertTrue(context["strong_auth_change"])
        self.assertTrue(context["loan_related"])
        self.assertEqual(context["limit_action_count"], 3)
        self.assertTrue(context["all_limit_actions"])
        self.assertEqual(context["device_compromise_count"], 3)
        self.assertTrue(context["device_compromise_2plus"])
        self.assertTrue(context["new_or_rare_recipient"])
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
        self.assertFalse(context["suspension_release_only"])
        self.assertFalse(context["recipient_suspended_only"])
        self.assertTrue(context["vpn_or_roaming"])

    def test_legacy_card_signal_is_not_available_to_new_rules(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Channel"] = "ATM"

        context = self.builder.build(raw_data)

        self.assertTrue(context["card_context_proxy"])
        self.assertNotIn("card_context_proxy", RULE_CONTEXT_FIELDS)

    def test_severe_amount_requires_anomaly_and_additional_pressure(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Transaction_Amount"] = 9_000_000
        raw_data["Account_one_month_max_amount"] = 10_000_000

        context = self.builder.build(raw_data)

        self.assertTrue(context["balance_depletion"])
        self.assertTrue(context["daily_limit_pressure"])
        self.assertFalse(context["amount_anomaly"])
        self.assertFalse(context["severe_amount_context"])

    def test_impossible_travel_uses_inclusive_distance_and_two_hour_boundary(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Distance"] = 100
        raw_data["Time Difference"] = "0 days 02:00:00"
        self.assertTrue(self.builder.build(raw_data)["impossible_travel"])

        raw_data["Time Difference"] = "0 days 00:00:00"
        self.assertFalse(self.builder.build(raw_data)["impossible_travel"])
        raw_data["Time Difference"] = "0 days 02:00:01"
        self.assertFalse(self.builder.build(raw_data)["impossible_travel"])

    def test_recently_resumed_rejects_reversed_or_more_than_thirty_days(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Unused_account_status"] = 1
        raw_data["Transaction_resumed_date"] = "2026-07-08T14:30:00+09:00"
        self.assertTrue(self.builder.build(raw_data)["recently_resumed"])

        raw_data["Transaction_resumed_date"] = "2026-07-07T14:30:00+09:00"
        self.assertFalse(self.builder.build(raw_data)["recently_resumed"])
        raw_data["Transaction_resumed_date"] = "2026-08-08T14:30:00+09:00"
        self.assertFalse(self.builder.build(raw_data)["recently_resumed"])

    def test_amount_anomaly_uses_strict_monthly_baseline_comparison(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Account_one_month_max_amount"] = 300
        raw_data["Account_one_month_std_dev"] = 100
        raw_data["Transaction_Amount"] = 300
        self.assertFalse(self.builder.build(raw_data)["amount_anomaly"])

        raw_data["Transaction_Amount"] = 301
        self.assertTrue(self.builder.build(raw_data)["amount_anomaly"])

    def test_accepts_legacy_account_release_alias_and_normalizes_name(self) -> None:
        raw_data = valid_rule_raw_data()
        value = raw_data.pop(ACCOUNT_RELEASE_FIELD)
        raw_data[LEGACY_ACCOUNT_RELEASE_FIELD] = value

        context = self.builder.build(raw_data)

        self.assertEqual(context[ACCOUNT_RELEASE_FIELD], 0)
        self.assertNotIn(LEGACY_ACCOUNT_RELEASE_FIELD, context)

    def test_rejects_conflicting_account_release_aliases(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data[LEGACY_ACCOUNT_RELEASE_FIELD] = 1

        with self.assertRaisesRegex(RuleFeatureError, "서로 다른 값"):
            self.builder.build(raw_data)

    def test_rejects_missing_required_rule_feature(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data.pop("Customer_loan_type")

        with self.assertRaisesRegex(RuleFeatureError, "Customer_loan_type"):
            self.builder.build(raw_data)

    def test_rejects_non_binary_flag_and_negative_duration(self) -> None:
        raw_data = valid_rule_raw_data()
        raw_data["Account_indicator_Openbanking"] = 2
        with self.assertRaisesRegex(RuleFeatureError, "0 또는 1"):
            self.builder.build(raw_data)

        raw_data["Account_indicator_Openbanking"] = 0
        raw_data["Time Difference"] = "-0 days 00:00:01"
        with self.assertRaisesRegex(RuleFeatureError, "음수"):
            self.builder.build(raw_data)


if __name__ == "__main__":
    unittest.main()
