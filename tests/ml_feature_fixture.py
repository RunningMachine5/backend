"""Backend↔ML raw59/raw64 계약 테스트용 거래 한 건."""

from typing import Any


def valid_ml_raw_data() -> dict[str, Any]:
    """ML 담당자의 정식 추론 raw59를 반환한다."""

    return {
        "customer_birth_date": "1984-08-14T00:00:00",
        "customer_gender": "female",
        "customer_name": "test-customer",
        "customer_registration_datetime": "2020-03-14T10:30:00",
        "customer_credit_rating": 5,
        "customer_flag_change_of_authentication_1": False,
        "customer_flag_change_of_authentication_2": False,
        "customer_flag_change_of_authentication_3": False,
        "customer_flag_change_of_authentication_4": False,
        "customer_rooting_jailbreak_indicator": False,
        "customer_mobile_roaming_indicator": False,
        "customer_vpn_indicator": False,
        "customer_loan_type": "b",
        "customer_flag_terminal_malicious_behavior_1": False,
        "customer_flag_terminal_malicious_behavior_2": False,
        "customer_flag_terminal_malicious_behavior_3": False,
        "customer_flag_terminal_malicious_behavior_5": False,
        "customer_flag_terminal_malicious_behavior_6": False,
        "customer_inquery_atm_limit": False,
        "customer_increase_atm_limit": False,
        "account_account_number": "123456789400",
        "account_account_type": "a",
        "account_creation_datetime": "2020-03-15T09:00:00",
        "account_initial_balance": 10_000_000,
        "account_balance": 8_500_000,
        "account_indicator_release_limit_excess": 0,
        "account_amount_daily_limit": 3_000_000,
        "account_indicator_openbanking": True,
        "account_remaining_amount_daily_limit_exceeded": 2_000_000,
        "account_release_suspention": False,
        "account_one_month_max_amount": 500_000,
        "account_one_month_std_dev": 120_000.0,
        "account_dawn_one_month_max_amount": 150_000,
        "account_dawn_one_month_std_dev": 40_000.0,
        "transaction_datetime": "2026-08-13T06:00:00",
        "transaction_amount": 75_000,
        "channel": "ATM",
        "operating_system": "iOS",
        "error_code": "none",
        "type_general_automatic": "general",
        "ip_address": "203.0.113.40",
        "mac_address": "00:1A:2B:3C:4D:40",
        "access_medium": "a",
        "location": "seoul",
        "recipient_account_number": "987654321400",
        "transaction_num_connection_failure": 0,
        "another_person_account": False,
        "distance": 1.5,
        "time_difference": 90,
        "unused_terminal_status": False,
        "last_atm_transaction_datetime": None,
        "last_bank_branch_transaction_datetime": "2026-07-21T11:10:00",
        "flag_deposit_more_than_ten_million": True,
        "unused_account_status": False,
        "recipient_account_suspend_status": False,
        "number_of_transaction_with_the_account": 36,
        "transaction_history_with_the_account": 36,
        "first_time_ios_by_vulnerable_user": False,
        "transaction_resumed_date": None,
    }


def valid_transaction_row(
    transaction_id: str = "T00000001",
    *,
    is_fraud: bool | None = None,
) -> dict[str, Any]:
    """train1.csv와 같은 flat raw64-compatible 한 행을 반환한다."""

    row = {
        "transaction_id": transaction_id,
        **valid_ml_raw_data(),
        "customer_identification_number": "upTALE-VwSUVKY",
        "customer_id": "C000494",
        "balance_drain_ratio": 0.15,
    }
    if is_fraud is not None:
        row["is_fraud"] = is_fraud
    return row
