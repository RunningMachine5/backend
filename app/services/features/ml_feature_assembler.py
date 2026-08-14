"""정규화 테이블과 ML 담당자의 raw59 Feature 계약 사이 변환."""

from __future__ import annotations

import re
from datetime import timedelta

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.dto.ml_features import LOCATION_PATTERN, MLTransactionFeatures


class FeatureAssemblyError(ValueError):
    """저장된 행에서 raw59 계약을 복원하지 못한 경우."""


_TIME_DIFFERENCE_PATTERN = re.compile(
    r"^\s*(?P<sign>-)?(?:(?P<days>\d+)\s+days?\s+)?"
    r"(?P<hours>\d+):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\s*$"
)


def parse_time_difference(value: str) -> timedelta:
    """``N days HH:MM:SS`` 문자열을 DB interval로 바꾼다."""

    match = _TIME_DIFFERENCE_PATTERN.fullmatch(value)
    if match is None:
        raise FeatureAssemblyError(
            "time_difference must use the 'N days HH:MM:SS' format"
        )
    delta = timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours")),
        minutes=int(match.group("minutes")),
        seconds=int(match.group("seconds")),
    )
    return -delta if match.group("sign") else delta


def format_time_difference(value: timedelta) -> str:
    """DB interval을 ML 원본 CSV와 같은 문자열로 되돌린다."""

    total_seconds = int(value.total_seconds())
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{days} days {hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_location(value: str) -> tuple[float | None, float | None]:
    """위치 문자열 끝의 위도·경도를 검색용 컬럼으로 분리한다."""

    match = LOCATION_PATTERN.search(value.strip())
    if match is None:
        return None, None
    return float(match.group("latitude")), float(match.group("longitude"))


def build_customer_fields(features: MLTransactionFeatures) -> dict[str, object]:
    return {
        "birth_date": features.customer_birth_date.date(),
        "gender": features.customer_gender,
        "registration_datetime": features.customer_registration_datetime,
        "credit_rating": features.customer_credit_rating,
        "loan_type": features.customer_loan_type,
    }


def build_account_fields(features: MLTransactionFeatures) -> dict[str, object]:
    return {
        "account_type": features.account_account_type,
        "creation_datetime": features.account_creation_datetime,
        "amount_daily_limit": features.account_amount_daily_limit,
        "indicator_openbanking": features.account_indicator_openbanking,
        "indicator_release_limit_excess": bool(
            features.account_indicator_release_limit_excess
        ),
        "current_balance": features.account_balance,
        "remaining_daily_limit": (
            features.account_remaining_amount_daily_limit_exceeded
        ),
    }


def build_transaction_fields(
    features: MLTransactionFeatures,
) -> dict[str, object]:
    latitude, longitude = parse_location(features.location)
    return {
        "transaction_datetime": features.transaction_datetime,
        "transaction_amount": features.transaction_amount,
        "channel": features.channel.lower(),
        "type_general_automatic": features.type_general_automatic.lower(),
        "access_medium": (
            features.access_medium.lower() if features.access_medium else None
        ),
        "error_code": features.error_code,
        "num_connection_failure": features.transaction_num_connection_failure,
        "another_person_account": features.another_person_account,
        "initial_balance": features.account_initial_balance,
        "balance": features.account_balance,
        "remaining_amount_daily_limit_exceeded": (
            features.account_remaining_amount_daily_limit_exceeded
        ),
        "operating_system": features.operating_system,
        "location": features.location,
        "location_lat": latitude,
        "location_lon": longitude,
        "rooting_jailbreak_indicator": (features.customer_rooting_jailbreak_indicator),
        "mobile_roaming_indicator": features.customer_mobile_roaming_indicator,
        "vpn_indicator": features.customer_vpn_indicator,
        "flag_terminal_malicious_behavior_1": (
            features.customer_flag_terminal_malicious_behavior_1
        ),
        "flag_terminal_malicious_behavior_2": (
            features.customer_flag_terminal_malicious_behavior_2
        ),
        "flag_terminal_malicious_behavior_3": (
            features.customer_flag_terminal_malicious_behavior_3
        ),
        "flag_terminal_malicious_behavior_5": (
            features.customer_flag_terminal_malicious_behavior_5
        ),
        "flag_terminal_malicious_behavior_6": (
            features.customer_flag_terminal_malicious_behavior_6
        ),
    }


def build_derived_features_fields(
    features: MLTransactionFeatures,
) -> dict[str, object]:
    return {
        "distance": features.distance,
        "time_difference": features.time_difference,
        "one_month_max_amount": features.account_one_month_max_amount,
        "one_month_std_dev": features.account_one_month_std_dev,
        "dawn_one_month_max_amount": features.account_dawn_one_month_max_amount,
        "dawn_one_month_std_dev": features.account_dawn_one_month_std_dev,
        "unused_terminal_status": features.unused_terminal_status,
        "unused_account_status": features.unused_account_status,
        "flag_deposit_more_than_tenMillion": (
            features.flag_deposit_more_than_ten_million
        ),
        "number_of_transaction_with_the_account": (
            features.number_of_transaction_with_the_account
        ),
        "transaction_history_with_the_account": (
            features.transaction_history_with_the_account
        ),
        "last_atm_transaction_datetime": features.last_atm_transaction_datetime,
        "last_bank_branch_transaction_datetime": (
            features.last_bank_branch_transaction_datetime
        ),
        "flag_change_of_authentication_1": (
            features.customer_flag_change_of_authentication_1
        ),
        "flag_change_of_authentication_2": (
            features.customer_flag_change_of_authentication_2
        ),
        "flag_change_of_authentication_3": (
            features.customer_flag_change_of_authentication_3
        ),
        "flag_change_of_authentication_4": (
            features.customer_flag_change_of_authentication_4
        ),
        "inquiry_atm_limit": features.customer_inquery_atm_limit,
        "increase_atm_limit": features.customer_increase_atm_limit,
        "release_suspension": features.account_release_suspention,
        "transaction_resumed_date": features.transaction_resumed_date,
        "recipient_account_suspend_status": (features.recipient_account_suspend_status),
        "first_time_ios_by_vulnerable_user": (
            features.first_time_ios_by_vulnerable_user
        ),
    }


def assemble_ml_features(
    *,
    customer: Customer,
    source_account: Account,
    recipient_account: Account | None,
    transaction: Transaction,
    derived: DerivedFeatures,
) -> MLTransactionFeatures:
    """정규화된 거래 스냅샷에서 exact raw59 객체를 복원한다."""

    required_account_values = {
        "account_type": source_account.account_type,
        "creation_datetime": source_account.creation_datetime,
        "amount_daily_limit": source_account.amount_daily_limit,
    }
    missing = [name for name, value in required_account_values.items() if value is None]
    if missing:
        raise FeatureAssemblyError(
            f"source account {source_account.account_number} is missing {missing}"
        )
    if recipient_account is None:
        raise FeatureAssemblyError(
            "recipient account is required by the raw59 contract"
        )
    return MLTransactionFeatures(
        customer_birth_date=customer.birth_date,
        customer_gender=customer.gender,
        customer_name=customer.name,
        customer_registration_datetime=customer.registration_datetime,
        customer_credit_rating=customer.credit_rating,
        customer_flag_change_of_authentication_1=(
            derived.flag_change_of_authentication_1
        ),
        customer_flag_change_of_authentication_2=(
            derived.flag_change_of_authentication_2
        ),
        customer_flag_change_of_authentication_3=(
            derived.flag_change_of_authentication_3
        ),
        customer_flag_change_of_authentication_4=(
            derived.flag_change_of_authentication_4
        ),
        customer_rooting_jailbreak_indicator=(transaction.rooting_jailbreak_indicator),
        customer_mobile_roaming_indicator=transaction.mobile_roaming_indicator,
        customer_vpn_indicator=transaction.vpn_indicator,
        customer_loan_type=customer.loan_type,
        customer_flag_terminal_malicious_behavior_1=(
            transaction.flag_terminal_malicious_behavior_1
        ),
        customer_flag_terminal_malicious_behavior_2=(
            transaction.flag_terminal_malicious_behavior_2
        ),
        customer_flag_terminal_malicious_behavior_3=(
            transaction.flag_terminal_malicious_behavior_3
        ),
        customer_flag_terminal_malicious_behavior_5=(
            transaction.flag_terminal_malicious_behavior_5
        ),
        customer_flag_terminal_malicious_behavior_6=(
            transaction.flag_terminal_malicious_behavior_6
        ),
        customer_inquery_atm_limit=derived.inquiry_atm_limit,
        customer_increase_atm_limit=derived.increase_atm_limit,
        account_account_number=source_account.account_number,
        account_account_type=source_account.account_type,
        account_creation_datetime=source_account.creation_datetime,
        account_initial_balance=transaction.initial_balance,
        account_balance=transaction.balance,
        account_indicator_release_limit_excess=int(
            bool(source_account.indicator_release_limit_excess)
        ),
        account_amount_daily_limit=source_account.amount_daily_limit,
        account_indicator_openbanking=bool(source_account.indicator_openbanking),
        account_remaining_amount_daily_limit_exceeded=(
            transaction.remaining_amount_daily_limit_exceeded
        ),
        account_release_suspention=derived.release_suspension,
        account_one_month_max_amount=derived.one_month_max_amount,
        account_one_month_std_dev=derived.one_month_std_dev,
        account_dawn_one_month_max_amount=derived.dawn_one_month_max_amount,
        account_dawn_one_month_std_dev=derived.dawn_one_month_std_dev,
        transaction_datetime=transaction.transaction_datetime,
        transaction_amount=transaction.transaction_amount,
        channel=transaction.channel,
        operating_system=transaction.operating_system,
        error_code=transaction.error_code or "",
        type_general_automatic=transaction.type_general_automatic,
        ip_address=(str(transaction.ip_address) if transaction.ip_address else None),
        mac_address=(str(transaction.mac_address) if transaction.mac_address else None),
        access_medium=transaction.access_medium,
        location=transaction.location,
        recipient_account_number=recipient_account.account_number,
        transaction_num_connection_failure=transaction.num_connection_failure,
        another_person_account=transaction.another_person_account,
        distance=derived.distance,
        time_difference=derived.time_difference,
        unused_terminal_status=derived.unused_terminal_status,
        last_atm_transaction_datetime=derived.last_atm_transaction_datetime,
        last_bank_branch_transaction_datetime=(
            derived.last_bank_branch_transaction_datetime
        ),
        flag_deposit_more_than_ten_million=(derived.flag_deposit_more_than_tenMillion),
        unused_account_status=derived.unused_account_status,
        recipient_account_suspend_status=(derived.recipient_account_suspend_status),
        number_of_transaction_with_the_account=(
            derived.number_of_transaction_with_the_account
        ),
        transaction_history_with_the_account=(
            derived.transaction_history_with_the_account
        ),
        first_time_ios_by_vulnerable_user=(derived.first_time_ios_by_vulnerable_user),
        transaction_resumed_date=derived.transaction_resumed_date,
    )


__all__ = [
    "FeatureAssemblyError",
    "assemble_ml_features",
    "build_account_fields",
    "build_customer_fields",
    "build_derived_features_fields",
    "build_transaction_fields",
    "format_time_difference",
    "parse_location",
    "parse_time_difference",
]
