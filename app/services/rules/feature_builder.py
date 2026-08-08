"""고정된 거래 원본 피처에서 룰 평가용 컨텍스트를 만든다."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any


class RuleFeatureError(ValueError):
    """룰 평가에 필요한 원본 피처가 없거나 올바르지 않을 때 발생한다."""


ACCOUNT_RELEASE_FIELD = "Account_release_suspension"
LEGACY_ACCOUNT_RELEASE_FIELD = "Account_release_suspention"
TIME_DIFFERENCE_FIELD = "Time Difference"

RULE_RAW_FEATURES = (
    "Customer_Birthyear",
    "Transaction_Datetime",
    "Customer_loan_type",
    "Customer_inquery_atm_limit",
    "Customer_increase_atm_limit",
    "Customer_flag_terminal_malicious_behavior_1",
    "Customer_flag_terminal_malicious_behavior_2",
    "Customer_flag_terminal_malicious_behavior_3",
    "Customer_flag_terminal_malicious_behavior_5",
    "Customer_flag_terminal_malicious_behavior_6",
    "Customer_rooting_jailbreak_indicator",
    "Customer_VPN_Indicator",
    "Customer_mobile_roaming_indicator",
    "Customer_flag_change_of_authentication_1",
    "Customer_flag_change_of_authentication_2",
    "Customer_flag_change_of_authentication_3",
    "Customer_flag_change_of_authentication_4",
    "Channel",
    "Operating_System",
    "Access_Medium",
    "Transaction_num_connection_failure",
    "Transaction_Amount",
    "Account_initial_balance",
    "Account_balance",
    "Account_indicator_release_limit_excess",
    "Account_amount_daily_limit",
    "Account_remaining_amount_daily_limit_exceeded",
    "Account_one_month_max_amount",
    "Account_one_month_std_dev",
    ACCOUNT_RELEASE_FIELD,
    "Recipient_account_suspend_status",
    "Unused_account_status",
    "Transaction_resumed_date",
    "Another_Person_Account",
    "Transaction_history_with_the_account",
    "Number_of_transaction_with_the_account",
    "Flag_deposit_more_than_tenMillion",
    "Distance",
    TIME_DIFFERENCE_FIELD,
    "Unused_terminal_status",
    "Type_General_Automatic",
    "Account_indicator_Openbanking",
    "First_time_iOS_by_vulnerable_user",
)

RULE_DERIVED_FEATURES = (
    "transaction_age",
    "authentication_changed",
    "loan_related",
    "device_compromise_count",
    "new_or_rare_recipient",
    "rapid_repeat",
    "amount_anomaly",
    "impossible_travel",
    "recently_resumed",
    "card_context_proxy",
    "phone_number_manipulation",
    "remote_control",
    "limit_adjustment_detected",
    "high_value_or_balance_pressure",
    "vulnerable_mobile_environment",
    "new_recipient_transfer",
    "account_suspension_released",
    "recipient_account_suspended",
    "vpn_or_roaming",
)

RULE_CONTEXT_FIELDS = frozenset((*RULE_RAW_FEATURES, *RULE_DERIVED_FEATURES))

_BINARY_FIELDS = (
    "Customer_inquery_atm_limit",
    "Customer_increase_atm_limit",
    "Customer_flag_terminal_malicious_behavior_1",
    "Customer_flag_terminal_malicious_behavior_2",
    "Customer_flag_terminal_malicious_behavior_3",
    "Customer_flag_terminal_malicious_behavior_5",
    "Customer_flag_terminal_malicious_behavior_6",
    "Customer_rooting_jailbreak_indicator",
    "Customer_VPN_Indicator",
    "Customer_mobile_roaming_indicator",
    "Customer_flag_change_of_authentication_1",
    "Customer_flag_change_of_authentication_2",
    "Customer_flag_change_of_authentication_3",
    "Customer_flag_change_of_authentication_4",
    "Account_indicator_release_limit_excess",
    ACCOUNT_RELEASE_FIELD,
    "Recipient_account_suspend_status",
    "Unused_account_status",
    "Another_Person_Account",
    "Flag_deposit_more_than_tenMillion",
    "Unused_terminal_status",
    "Account_indicator_Openbanking",
    "First_time_iOS_by_vulnerable_user",
)

_INTEGER_FIELDS = (
    "Customer_Birthyear",
    "Transaction_num_connection_failure",
    "Transaction_history_with_the_account",
    "Number_of_transaction_with_the_account",
)

_NUMBER_FIELDS = (
    "Transaction_Amount",
    "Account_initial_balance",
    "Account_balance",
    "Account_amount_daily_limit",
    "Account_remaining_amount_daily_limit_exceeded",
    "Account_one_month_max_amount",
    "Account_one_month_std_dev",
    "Distance",
)

_TIMEDELTA_PATTERN = re.compile(
    r"^\s*(?P<sign>-)?(?:(?P<days>\d+)\s+days?\s+)?"
    r"(?P<hours>\d+):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\s*$"
)


class RuleFeatureBuilder:
    """최종 사기유형 규칙에 필요한 원본값과 공통 파생 신호를 만든다.

    관리자가 임의의 거래 컬럼을 참조하지 못하도록 룰 컨텍스트를 허용 목록으로
    고정한다. 정지해제 컬럼은 올바른 철자를 내부 표준으로 사용하면서 기존
    Backend·ML 계약의 오타 이름도 입력 호환을 위해 수용한다.
    """

    def build(self, raw_data: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_data, Mapping):
            raise RuleFeatureError("거래 원본은 key-value 매핑이어야 합니다.")

        normalized = self._normalize_raw_features(raw_data)
        transaction_datetime = self._as_datetime(
            "Transaction_Datetime",
            normalized["Transaction_Datetime"],
        )
        resumed_datetime = self._as_optional_datetime(
            "Transaction_resumed_date",
            normalized["Transaction_resumed_date"],
        )

        for field_name in _INTEGER_FIELDS:
            normalized[field_name] = self._as_int(field_name, normalized[field_name])
        for field_name in _NUMBER_FIELDS:
            normalized[field_name] = self._as_number(
                field_name,
                normalized[field_name],
            )
        for field_name in _BINARY_FIELDS:
            normalized[field_name] = self._as_binary_flag(
                field_name,
                normalized[field_name],
            )

        birthyear = normalized["Customer_Birthyear"]
        if birthyear > transaction_datetime.year:
            raise RuleFeatureError(
                "Customer_Birthyear는 Transaction_Datetime의 연도보다 클 수 없습니다."
            )

        loan_type = self._as_enum(
            "Customer_loan_type",
            normalized["Customer_loan_type"],
            {"a", "b", "c", "d", "e"},
        )
        channel = self._canonical_enum(
            "Channel",
            normalized["Channel"],
            {
                "mobile": "mobile",
                "internet": "internet",
                "atm": "ATM",
                "others": "Others",
            },
        )
        operating_system = self._canonical_enum(
            "Operating_System",
            normalized["Operating_System"],
            {
                "android": "Android",
                "ios": "iOS",
                "windows": "Windows",
                "macos": "macOS",
                "linux": "Linux",
                "others": "Others",
            },
        )
        access_medium = self._as_enum(
            "Access_Medium",
            normalized["Access_Medium"],
            set("abcdefgh"),
        )
        transaction_type = self._as_enum(
            "Type_General_Automatic",
            normalized["Type_General_Automatic"],
            {"general", "automatic"},
        )
        time_difference_seconds = self._as_duration_seconds(
            TIME_DIFFERENCE_FIELD,
            normalized[TIME_DIFFERENCE_FIELD],
        )

        normalized.update(
            {
                "Transaction_Datetime": transaction_datetime,
                "Transaction_resumed_date": resumed_datetime,
                "Customer_loan_type": loan_type,
                "Channel": channel,
                "Operating_System": operating_system,
                "Access_Medium": access_medium,
                "Type_General_Automatic": transaction_type,
            }
        )

        age = transaction_datetime.year - birthyear
        authentication_changed = any(
            normalized[f"Customer_flag_change_of_authentication_{number}"] == 1
            for number in range(1, 5)
        )
        loan_related = loan_type in {"b", "c", "d", "e"}
        device_compromise_count = sum(
            (
                normalized["Customer_flag_terminal_malicious_behavior_3"] == 1,
                normalized["Customer_flag_terminal_malicious_behavior_5"] == 1,
                normalized["Customer_flag_terminal_malicious_behavior_6"] == 1,
                normalized["Customer_rooting_jailbreak_indicator"] == 1,
            )
        )
        new_or_rare_recipient = (
            normalized["Transaction_history_with_the_account"] <= 1
        )
        rapid_repeat = normalized["Number_of_transaction_with_the_account"] >= 3

        transaction_amount = abs(normalized["Transaction_Amount"])
        monthly_max = abs(normalized["Account_one_month_max_amount"])
        monthly_std = abs(normalized["Account_one_month_std_dev"])
        amount_anomaly = transaction_amount > max(
            monthly_max,
            3 * max(monthly_std, 1),
        )
        high_value_or_balance_pressure = (
            amount_anomaly
            or transaction_amount
            >= 0.8 * max(abs(normalized["Account_initial_balance"]), 1)
            or normalized["Account_balance"] < 0
            or transaction_amount
            >= 0.8 * max(normalized["Account_amount_daily_limit"], 1)
            or normalized["Account_remaining_amount_daily_limit_exceeded"]
            <= 0.1 * max(normalized["Account_amount_daily_limit"], 1)
        )
        impossible_travel = (
            normalized["Distance"] >= 100
            and 0 < time_difference_seconds <= 2 * 60 * 60
        )
        recently_resumed = self._recently_resumed(
            unused_account=normalized["Unused_account_status"] == 1,
            transaction_datetime=transaction_datetime,
            resumed_datetime=resumed_datetime,
        )
        card_context_proxy = (
            channel.lower() in {"atm", "others"}
            and normalized["Another_Person_Account"] == 0
            and not loan_related
        )
        mobile_environment = (
            channel.lower() == "mobile"
            or operating_system.lower() in {"android", "ios"}
        )
        vulnerable_mobile_environment = (
            age >= 60 and mobile_environment
        ) or normalized["First_time_iOS_by_vulnerable_user"] == 1
        account_suspension_released = normalized[ACCOUNT_RELEASE_FIELD] == 1
        recipient_account_suspended = (
            normalized["Recipient_account_suspend_status"] == 1
        )

        return {
            **normalized,
            "transaction_age": age,
            "authentication_changed": authentication_changed,
            "loan_related": loan_related,
            "device_compromise_count": device_compromise_count,
            "new_or_rare_recipient": new_or_rare_recipient,
            "rapid_repeat": rapid_repeat,
            "amount_anomaly": amount_anomaly,
            "impossible_travel": impossible_travel,
            "recently_resumed": recently_resumed,
            "card_context_proxy": card_context_proxy,
            "phone_number_manipulation": (
                normalized["Customer_flag_terminal_malicious_behavior_1"] == 1
            ),
            "remote_control": (
                normalized["Customer_flag_terminal_malicious_behavior_2"] == 1
            ),
            "limit_adjustment_detected": any(
                (
                    normalized["Customer_inquery_atm_limit"] == 1,
                    normalized["Customer_increase_atm_limit"] == 1,
                    normalized["Account_indicator_release_limit_excess"] == 1,
                )
            ),
            "high_value_or_balance_pressure": high_value_or_balance_pressure,
            "vulnerable_mobile_environment": vulnerable_mobile_environment,
            "new_recipient_transfer": (
                new_or_rare_recipient
                and normalized["Another_Person_Account"] == 1
            ),
            "account_suspension_released": account_suspension_released,
            "recipient_account_suspended": recipient_account_suspended,
            "vpn_or_roaming": (
                normalized["Customer_VPN_Indicator"] == 1
                or normalized["Customer_mobile_roaming_indicator"] == 1
            ),
        }

    def _normalize_raw_features(
        self,
        raw_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name in RULE_RAW_FEATURES:
            if field_name == ACCOUNT_RELEASE_FIELD:
                normalized[field_name] = self._account_release_value(raw_data)
                continue
            if field_name not in raw_data:
                raise RuleFeatureError(f"필수 룰 피처가 없습니다: {field_name}")
            normalized[field_name] = raw_data[field_name]
        return normalized

    @staticmethod
    def _account_release_value(raw_data: Mapping[str, Any]) -> Any:
        has_canonical = ACCOUNT_RELEASE_FIELD in raw_data
        has_legacy = LEGACY_ACCOUNT_RELEASE_FIELD in raw_data
        if not has_canonical and not has_legacy:
            raise RuleFeatureError(
                f"필수 룰 피처가 없습니다: {ACCOUNT_RELEASE_FIELD}"
            )

        canonical_value = raw_data.get(ACCOUNT_RELEASE_FIELD)
        legacy_value = raw_data.get(LEGACY_ACCOUNT_RELEASE_FIELD)
        if has_canonical and has_legacy and canonical_value != legacy_value:
            raise RuleFeatureError(
                "정지해제 컬럼의 신규·기존 이름에 서로 다른 값이 전달되었습니다."
            )
        return canonical_value if has_canonical else legacy_value

    @staticmethod
    def _as_datetime(field_name: str, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RuleFeatureError(
                    f"{field_name}은 ISO-8601 날짜·시간이어야 합니다."
                ) from exc
        raise RuleFeatureError(f"{field_name}은 날짜·시간이어야 합니다.")

    @classmethod
    def _as_optional_datetime(cls, field_name: str, value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        return cls._as_datetime(field_name, value)

    @staticmethod
    def _as_int(field_name: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuleFeatureError(f"{field_name}은 정수여야 합니다.")
        return value

    @staticmethod
    def _as_number(field_name: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuleFeatureError(f"{field_name}은 숫자여야 합니다.")
        number = float(value)
        if not math.isfinite(number):
            raise RuleFeatureError(f"{field_name}은 유한한 숫자여야 합니다.")
        return number

    @staticmethod
    def _as_binary_flag(field_name: str, value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int) and value in {0, 1}:
            return value
        raise RuleFeatureError(f"{field_name}은 0 또는 1이어야 합니다.")

    @staticmethod
    def _as_enum(field_name: str, value: Any, allowed: set[str]) -> str:
        if not isinstance(value, str):
            raise RuleFeatureError(f"{field_name}은 문자열이어야 합니다.")
        normalized = value.strip().lower()
        if normalized not in allowed:
            choices = ", ".join(sorted(allowed))
            raise RuleFeatureError(f"{field_name}은 {choices} 중 하나여야 합니다.")
        return normalized

    @staticmethod
    def _canonical_enum(
        field_name: str,
        value: Any,
        canonical_by_lower: Mapping[str, str],
    ) -> str:
        if not isinstance(value, str):
            raise RuleFeatureError(f"{field_name}은 문자열이어야 합니다.")
        normalized = value.strip().lower()
        try:
            return canonical_by_lower[normalized]
        except KeyError as exc:
            choices = ", ".join(canonical_by_lower.values())
            raise RuleFeatureError(
                f"{field_name}은 {choices} 중 하나여야 합니다."
            ) from exc

    @staticmethod
    def _as_duration_seconds(field_name: str, value: Any) -> int:
        if isinstance(value, timedelta):
            total_seconds = value.total_seconds()
        elif isinstance(value, str):
            match = _TIMEDELTA_PATTERN.fullmatch(value)
            if match is None:
                raise RuleFeatureError(
                    f"{field_name}은 'N days HH:MM:SS' 형식이어야 합니다."
                )
            total_seconds = (
                int(match.group("days") or 0) * 24 * 60 * 60
                + int(match.group("hours")) * 60 * 60
                + int(match.group("minutes")) * 60
                + int(match.group("seconds"))
            )
            if match.group("sign"):
                total_seconds *= -1
        else:
            raise RuleFeatureError(f"{field_name}은 시간 간격 문자열이어야 합니다.")

        if not math.isfinite(total_seconds) or total_seconds != int(total_seconds):
            raise RuleFeatureError(f"{field_name}은 정수 초 단위여야 합니다.")
        if total_seconds < 0:
            raise RuleFeatureError(f"{field_name}은 음수일 수 없습니다.")
        return int(total_seconds)

    @staticmethod
    def _recently_resumed(
        *,
        unused_account: bool,
        transaction_datetime: datetime,
        resumed_datetime: datetime | None,
    ) -> bool:
        if not unused_account or resumed_datetime is None:
            return False
        try:
            elapsed_days = (transaction_datetime - resumed_datetime).days
        except TypeError as exc:
            raise RuleFeatureError(
                "Transaction_Datetime과 Transaction_resumed_date의 시간대 형식이 다릅니다."
            ) from exc
        return 0 <= elapsed_days <= 30


__all__ = [
    "ACCOUNT_RELEASE_FIELD",
    "LEGACY_ACCOUNT_RELEASE_FIELD",
    "RULE_CONTEXT_FIELDS",
    "RULE_DERIVED_FEATURES",
    "RULE_RAW_FEATURES",
    "TIME_DIFFERENCE_FIELD",
    "RuleFeatureBuilder",
    "RuleFeatureError",
]
