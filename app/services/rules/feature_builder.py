"""ML raw60 계약에서 룰 평가용 컨텍스트를 만든다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.dto.ml_features import MLTransactionFeatures


# ML ``PredictInputDTO`` 60개 중 상관관계용 transaction_id를 제외한 59개다.
# Backend와 ML을 서로 독립 배포할 수 있도록 여기에 계약을 명시적으로 고정한다.
RULE_RAW_FEATURES = (
    "customer_birth_date",
    "customer_gender",
    "customer_name",
    "customer_registration_datetime",
    "customer_credit_rating",
    "customer_flag_change_of_authentication_1",
    "customer_flag_change_of_authentication_2",
    "customer_flag_change_of_authentication_3",
    "customer_flag_change_of_authentication_4",
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_loan_type",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
    "customer_inquery_atm_limit",
    "customer_increase_atm_limit",
    "account_account_number",
    "account_account_type",
    "account_creation_datetime",
    "account_initial_balance",
    "account_balance",
    "account_indicator_release_limit_excess",
    "account_amount_daily_limit",
    "account_indicator_openbanking",
    "account_remaining_amount_daily_limit_exceeded",
    "account_release_suspention",
    "account_one_month_max_amount",
    "account_one_month_std_dev",
    "account_dawn_one_month_max_amount",
    "account_dawn_one_month_std_dev",
    "transaction_datetime",
    "transaction_amount",
    "channel",
    "operating_system",
    "error_code",
    "type_general_automatic",
    "ip_address",
    "mac_address",
    "access_medium",
    "location",
    "recipient_account_number",
    "transaction_num_connection_failure",
    "another_person_account",
    "distance",
    "time_difference",
    "unused_terminal_status",
    "last_atm_transaction_datetime",
    "last_bank_branch_transaction_datetime",
    "flag_deposit_more_than_ten_million",
    "unused_account_status",
    "recipient_account_suspend_status",
    "number_of_transaction_with_the_account",
    "transaction_history_with_the_account",
    "first_time_ios_by_vulnerable_user",
    "transaction_resumed_date",
)
RULE_RAW_FEATURE_SET = frozenset(RULE_RAW_FEATURES)

# 개인 식별값과 원본 위치·생년월일은 평가 입력에는 존재하지만 관리자가 직접
# 조건식에 사용할 수 없다. 관리자 registry는 이 목록을 제외한 필드만 노출한다.
RULE_REGISTRY_EXCLUDED_RAW_FEATURES = frozenset(
    {
        "customer_birth_date",
        "customer_name",
        "account_account_number",
        "ip_address",
        "mac_address",
        "location",
        "recipient_account_number",
    }
)

RULE_DERIVED_FEATURES = (
    "transaction_age",
    "authentication_change_count",
    "strong_auth_change",
    "loan_related",
    "limit_action_count",
    "all_limit_actions",
    "device_compromise_count",
    "device_compromise_2plus",
    "new_or_rare_recipient",
    "recipient_transfer",
    "rapid_repeat",
    "amount_anomaly",
    "balance_depletion",
    "daily_limit_pressure",
    "severe_amount_context",
    "loan_escalation_context",
    "impossible_travel",
    "recently_resumed",
    "phone_number_manipulation",
    "remote_control",
    "vulnerable_mobile",
    "account_suspension_released",
    "recipient_account_suspended",
    "suspension_pair",
    "suspension_release_only",
    "recipient_suspended_only",
    "vpn_or_roaming",
)

RULE_REGISTRY_RAW_FEATURES = tuple(
    field
    for field in RULE_RAW_FEATURES
    if field not in RULE_REGISTRY_EXCLUDED_RAW_FEATURES
)
RULE_CONTEXT_FIELDS = frozenset((*RULE_REGISTRY_RAW_FEATURES, *RULE_DERIVED_FEATURES))

# 운영 DB에 이미 저장된 ACTIVE 룰셋을 raw60 기본 룰셋으로 교체할 때까지만
# 평가 엔진이 수용하는 별칭이다. 신규 registry에는 절대 노출하지 않는다.
TRANSITION_LEGACY_RAW_ALIASES = {
    "Customer_Birthyear": "customer_birth_date",
    "Transaction_Datetime": "transaction_datetime",
    "Customer_loan_type": "customer_loan_type",
    "Customer_inquery_atm_limit": "customer_inquery_atm_limit",
    "Customer_increase_atm_limit": "customer_increase_atm_limit",
    "Customer_flag_terminal_malicious_behavior_1": "customer_flag_terminal_malicious_behavior_1",
    "Customer_flag_terminal_malicious_behavior_2": "customer_flag_terminal_malicious_behavior_2",
    "Customer_flag_terminal_malicious_behavior_3": "customer_flag_terminal_malicious_behavior_3",
    "Customer_flag_terminal_malicious_behavior_5": "customer_flag_terminal_malicious_behavior_5",
    "Customer_flag_terminal_malicious_behavior_6": "customer_flag_terminal_malicious_behavior_6",
    "Customer_rooting_jailbreak_indicator": "customer_rooting_jailbreak_indicator",
    "Customer_VPN_Indicator": "customer_vpn_indicator",
    "Customer_mobile_roaming_indicator": "customer_mobile_roaming_indicator",
    "Customer_flag_change_of_authentication_1": "customer_flag_change_of_authentication_1",
    "Customer_flag_change_of_authentication_2": "customer_flag_change_of_authentication_2",
    "Customer_flag_change_of_authentication_3": "customer_flag_change_of_authentication_3",
    "Customer_flag_change_of_authentication_4": "customer_flag_change_of_authentication_4",
    "Channel": "channel",
    "Operating_System": "operating_system",
    "Access_Medium": "access_medium",
    "Transaction_num_connection_failure": "transaction_num_connection_failure",
    "Transaction_Amount": "transaction_amount",
    "Account_initial_balance": "account_initial_balance",
    "Account_balance": "account_balance",
    "Account_indicator_release_limit_excess": "account_indicator_release_limit_excess",
    "Account_amount_daily_limit": "account_amount_daily_limit",
    "Account_remaining_amount_daily_limit_exceeded": "account_remaining_amount_daily_limit_exceeded",
    "Account_one_month_max_amount": "account_one_month_max_amount",
    "Account_one_month_std_dev": "account_one_month_std_dev",
    "Account_release_suspension": "account_release_suspention",
    "Account_release_suspention": "account_release_suspention",
    "Recipient_account_suspend_status": "recipient_account_suspend_status",
    "Unused_account_status": "unused_account_status",
    "Transaction_resumed_date": "transaction_resumed_date",
    "Another_Person_Account": "another_person_account",
    "Transaction_history_with_the_account": "transaction_history_with_the_account",
    "Number_of_transaction_with_the_account": "number_of_transaction_with_the_account",
    "Flag_deposit_more_than_tenMillion": "flag_deposit_more_than_ten_million",
    "Distance": "distance",
    "Time Difference": "time_difference",
    "Unused_terminal_status": "unused_terminal_status",
    "Type_General_Automatic": "type_general_automatic",
    "Account_indicator_Openbanking": "account_indicator_openbanking",
    "First_time_iOS_by_vulnerable_user": "first_time_ios_by_vulnerable_user",
}
TRANSITION_LEGACY_DERIVED_FEATURES = frozenset(
    {
        "authentication_changed",
        "card_context_proxy",
        "high_value_or_balance_pressure",
        "limit_adjustment_detected",
        "new_recipient_transfer",
        "vulnerable_mobile_environment",
    }
)
RULE_EVALUATION_FIELDS = frozenset(
    {
        *RULE_CONTEXT_FIELDS,
        *TRANSITION_LEGACY_RAW_ALIASES,
        *TRANSITION_LEGACY_DERIVED_FEATURES,
    }
)

_BINARY_FIELDS = (
    "customer_flag_change_of_authentication_1",
    "customer_flag_change_of_authentication_2",
    "customer_flag_change_of_authentication_3",
    "customer_flag_change_of_authentication_4",
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
    "customer_inquery_atm_limit",
    "customer_increase_atm_limit",
    "account_indicator_release_limit_excess",
    "account_indicator_openbanking",
    "account_release_suspention",
    "another_person_account",
    "unused_terminal_status",
    "flag_deposit_more_than_ten_million",
    "unused_account_status",
    "recipient_account_suspend_status",
    "first_time_ios_by_vulnerable_user",
)

class RuleFeatureBuilder:
    """조립 완료된 ML 거래 피처에서 룰 계산용 파생 신호를 만든다."""

    def build(
        self,
        features: MLTransactionFeatures,
    ) -> dict[str, Any]:
        normalized = features.model_dump(mode="python", by_alias=True)
        for field_name in _BINARY_FIELDS:
            normalized[field_name] = int(normalized[field_name])

        transaction_datetime = features.transaction_datetime
        birth_date = features.customer_birth_date
        time_difference_seconds = features.time_difference.total_seconds()
        normalized["time_difference"] = time_difference_seconds

        # 거래 API와 ML에는 입·출금 부호를 그대로 보낸다. 룰의 금액 임계값은
        # 방향이 아니라 거래 규모를 판단하므로 여기에서만 절댓값으로 바꾼다.
        normalized["transaction_amount"] = abs(float(features.transaction_amount))

        age = self._age_at_transaction(birth_date, transaction_datetime)
        authentication_change_count = sum(
            normalized[f"customer_flag_change_of_authentication_{number}"] == 1
            for number in range(1, 5)
        )
        strong_auth_change = authentication_change_count >= 3
        loan_related = normalized["customer_loan_type"] in {"b", "c", "d", "e"}
        limit_action_count = sum(
            (
                normalized["customer_inquery_atm_limit"] == 1,
                normalized["customer_increase_atm_limit"] == 1,
                normalized["account_indicator_release_limit_excess"] == 1,
            )
        )
        all_limit_actions = limit_action_count == 3
        device_compromise_count = sum(
            (
                normalized["customer_flag_terminal_malicious_behavior_3"] == 1,
                normalized["customer_flag_terminal_malicious_behavior_5"] == 1,
                normalized["customer_flag_terminal_malicious_behavior_6"] == 1,
                normalized["customer_rooting_jailbreak_indicator"] == 1,
            )
        )
        device_compromise_2plus = device_compromise_count >= 2
        new_or_rare_recipient = normalized["transaction_history_with_the_account"] <= 1
        recipient_transfer = (
            new_or_rare_recipient and normalized["another_person_account"] == 1
        )
        rapid_repeat = normalized["number_of_transaction_with_the_account"] >= 3

        transaction_amount = normalized["transaction_amount"]
        monthly_max = abs(normalized["account_one_month_max_amount"])
        monthly_std = abs(normalized["account_one_month_std_dev"])
        amount_anomaly = transaction_amount > max(
            monthly_max,
            3 * max(monthly_std, 1),
        )
        initial_balance = normalized["account_initial_balance"]
        account_balance = normalized["account_balance"]
        remaining_daily_limit = normalized[
            "account_remaining_amount_daily_limit_exceeded"
        ]
        balance_depletion = (
            initial_balance is not None
            and transaction_amount >= 0.8 * max(abs(initial_balance), 1)
        ) or (account_balance is not None and account_balance < 0)
        daily_limit_pressure = transaction_amount >= 0.8 * max(
            normalized["account_amount_daily_limit"], 1
        ) or (
            remaining_daily_limit is not None
            and remaining_daily_limit
            <= 0.1 * max(normalized["account_amount_daily_limit"], 1)
        )
        severe_amount_context = amount_anomaly and (
            balance_depletion or daily_limit_pressure
        )
        loan_escalation_context = loan_related and (
            normalized["customer_flag_terminal_malicious_behavior_1"] == 1
            or all_limit_actions
            or severe_amount_context
        )
        impossible_travel = (
            normalized["distance"] >= 100 and 0 < time_difference_seconds <= 2 * 60 * 60
        )
        recently_resumed = self._recently_resumed(
            unused_account=normalized["unused_account_status"] == 1,
            transaction_datetime=transaction_datetime,
            resumed_datetime=normalized["transaction_resumed_date"],
        )
        mobile_environment = normalized["channel"] == "mobile" or normalized[
            "operating_system"
        ] in {"android", "ios"}
        vulnerable_mobile = (age >= 60 and mobile_environment) or normalized[
            "first_time_ios_by_vulnerable_user"
        ] == 1
        account_suspension_released = normalized["account_release_suspention"] == 1
        recipient_account_suspended = (
            normalized["recipient_account_suspend_status"] == 1
        )
        suspension_pair = account_suspension_released and recipient_account_suspended
        suspension_release_only = (
            account_suspension_released and not recipient_account_suspended
        )
        recipient_suspended_only = (
            recipient_account_suspended and not account_suspension_released
        )
        vpn_or_roaming = (
            normalized["customer_vpn_indicator"] == 1
            or normalized["customer_mobile_roaming_indicator"] == 1
        )

        safe_raw_context = {
            field: normalized[field] for field in RULE_REGISTRY_RAW_FEATURES
        }
        context = {
            **safe_raw_context,
            "transaction_age": age,
            "authentication_change_count": authentication_change_count,
            "strong_auth_change": strong_auth_change,
            "loan_related": loan_related,
            "limit_action_count": limit_action_count,
            "all_limit_actions": all_limit_actions,
            "device_compromise_count": device_compromise_count,
            "device_compromise_2plus": device_compromise_2plus,
            "new_or_rare_recipient": new_or_rare_recipient,
            "recipient_transfer": recipient_transfer,
            "rapid_repeat": rapid_repeat,
            "amount_anomaly": amount_anomaly,
            "balance_depletion": balance_depletion,
            "daily_limit_pressure": daily_limit_pressure,
            "severe_amount_context": severe_amount_context,
            "loan_escalation_context": loan_escalation_context,
            "impossible_travel": impossible_travel,
            "recently_resumed": recently_resumed,
            "phone_number_manipulation": (
                normalized["customer_flag_terminal_malicious_behavior_1"] == 1
            ),
            "remote_control": (
                normalized["customer_flag_terminal_malicious_behavior_2"] == 1
            ),
            "vulnerable_mobile": vulnerable_mobile,
            "account_suspension_released": account_suspension_released,
            "recipient_account_suspended": recipient_account_suspended,
            "suspension_pair": suspension_pair,
            "suspension_release_only": suspension_release_only,
            "recipient_suspended_only": recipient_suspended_only,
            "vpn_or_roaming": vpn_or_roaming,
        }
        # 전환 기간에만 기존 ACTIVE 룰 정의를 실행할 수 있게 한다. 신규 룰은
        # RULE_CONTEXT_FIELDS 기반 registry만 사용하므로 이 이름을 생성할 수 없다.
        legacy_aliases = {
            alias: normalized[source]
            for alias, source in TRANSITION_LEGACY_RAW_ALIASES.items()
        }
        legacy_aliases["Customer_Birthyear"] = normalized["customer_birth_date"].year
        legacy_aliases["Channel"] = {
            "atm": "ATM",
            "others": "Others",
        }.get(normalized["channel"], normalized["channel"])
        legacy_aliases["Operating_System"] = {
            "android": "Android",
            "ios": "iOS",
            "windows": "Windows",
            "macos": "macOS",
            "linux": "Linux",
            "others": "Others",
        }.get(normalized["operating_system"], normalized["operating_system"])
        legacy_derived = {
            "authentication_changed": authentication_change_count >= 1,
            "card_context_proxy": (
                normalized["channel"] in {"atm", "others"}
                and normalized["another_person_account"] == 0
                and not loan_related
            ),
            "high_value_or_balance_pressure": (
                amount_anomaly or balance_depletion or daily_limit_pressure
            ),
            "limit_adjustment_detected": limit_action_count >= 1,
            "new_recipient_transfer": recipient_transfer,
            "vulnerable_mobile_environment": vulnerable_mobile,
        }
        return {**context, **legacy_aliases, **legacy_derived}

    @staticmethod
    def _age_at_transaction(
        birth_date: datetime, transaction_datetime: datetime
    ) -> int:
        return (
            transaction_datetime.year
            - birth_date.year
            - (
                (transaction_datetime.month, transaction_datetime.day)
                < (birth_date.month, birth_date.day)
            )
        )

    @staticmethod
    def _recently_resumed(
        *,
        unused_account: bool,
        transaction_datetime: datetime,
        resumed_datetime: datetime | None,
    ) -> bool:
        if not unused_account or resumed_datetime is None:
            return False
        elapsed_days = (transaction_datetime - resumed_datetime).days
        return 0 <= elapsed_days <= 30


if len(RULE_RAW_FEATURES) != 59:  # pragma: no cover - import invariant
    raise RuntimeError("RULE_RAW_FEATURES must contain exactly 59 columns.")


__all__ = [
    "RULE_CONTEXT_FIELDS",
    "RULE_DERIVED_FEATURES",
    "RULE_EVALUATION_FIELDS",
    "RULE_RAW_FEATURES",
    "RULE_RAW_FEATURE_SET",
    "RULE_REGISTRY_EXCLUDED_RAW_FEATURES",
    "RULE_REGISTRY_RAW_FEATURES",
    "TRANSITION_LEGACY_DERIVED_FEATURES",
    "TRANSITION_LEGACY_RAW_ALIASES",
    "RuleFeatureBuilder",
]
