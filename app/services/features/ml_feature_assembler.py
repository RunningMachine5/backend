"""평탄화된 원천 테이블과 ML 54개 Feature 계약 사이를 양방향 변환한다.

`transactions.raw_features` JSONB를 제거하면서 54개 Feature는 customers,
accounts, transactions, derived_features 네 테이블에 나뉘어 저장된다.
ML Serving 호출·룰 평가·학습 데이터셋 생성은 여전히 54개 Feature 계약을
요구하므로, 저장 시 분해하고 읽을 때 다시 조립하는 책임을 여기에 모은다.
"""

from __future__ import annotations

import re
from datetime import timedelta

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.dto.ml_prediction import LOCATION_PATTERN, MLTransactionFeatures


class FeatureAssemblyError(ValueError):
    """저장된 행에서 54개 Feature 계약을 복원하지 못한 경우."""


# 학습 CSV와 동일한 'N days HH:MM:SS' 표기. 일수가 0이어도 접두사를 유지한다.
_TIME_DIFFERENCE_PATTERN = re.compile(
    r"^\s*(?P<sign>-)?(?:(?P<days>\d+)\s+days?\s+)?"
    r"(?P<hours>\d+):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\s*$"
)


def parse_time_difference(value: str) -> timedelta:
    """'N days HH:MM:SS' 문자열을 timedelta로 바꾼다."""

    match = _TIME_DIFFERENCE_PATTERN.fullmatch(value)
    if match is None:
        raise FeatureAssemblyError(
            "Time Difference는 'N days HH:MM:SS' 형식이어야 합니다."
        )

    delta = timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours")),
        minutes=int(match.group("minutes")),
        seconds=int(match.group("seconds")),
    )
    return -delta if match.group("sign") else delta


def format_time_difference(value: timedelta) -> str:
    """timedelta를 학습 CSV와 같은 'N days HH:MM:SS' 문자열로 되돌린다."""

    total_seconds = int(value.total_seconds())
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    days, remainder = divmod(total_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{days} days {hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_location(value: str) -> tuple[float | None, float | None]:
    """'지역명... 위도 경도' 문자열에서 좌표만 분리한다.

    좌표 형식 검증은 MLTransactionFeatures.validate_location이 이미 수행하므로
    여기서는 조회용 컬럼을 채우는 데만 사용하고, 실패해도 None을 돌려준다.
    """

    match = LOCATION_PATTERN.search(value.strip())
    if match is None:
        return None, None
    return float(match.group("latitude")), float(match.group("longitude"))


def build_customer_fields(features: MLTransactionFeatures) -> dict[str, object]:
    """customers 테이블이 보관하는 고객 속성을 추출한다."""

    return {
        "birthyear": features.Customer_Birthyear,
        "gender": features.Customer_Gender,
        "registration_datetime": features.Customer_registration_datetime,
        "credit_rating": features.Customer_credit_rating,
        "loan_type": features.Customer_loan_type,
    }


def build_account_fields(features: MLTransactionFeatures) -> dict[str, object]:
    """accounts 테이블이 보관하는 출금 계좌 속성을 추출한다.

    current_balance / remaining_daily_limit은 거래 후 최신 상태를 반영한다.
    거래 시점 값은 transactions의 스냅샷 컬럼에 따로 남는다.
    """

    return {
        "account_type": features.Account_account_type,
        "creation_datetime": features.Account_creation_datetime,
        "amount_daily_limit": features.Account_amount_daily_limit,
        "indicator_openbanking": bool(features.Account_indicator_Openbanking),
        "indicator_release_limit_excess": bool(
            features.Account_indicator_release_limit_excess
        ),
        "current_balance": features.Account_balance,
        "remaining_daily_limit": (
            features.Account_remaining_amount_daily_limit_exceeded
        ),
    }


def build_transaction_fields(
    features: MLTransactionFeatures,
) -> dict[str, object]:
    """transactions 테이블이 보관하는 거래 원본과 스냅샷을 추출한다."""

    latitude, longitude = parse_location(features.Location)
    return {
        "transaction_datetime": features.Transaction_Datetime,
        "transaction_amount": features.Transaction_Amount,
        "channel": features.Channel,
        "type_general_automatic": features.Type_General_Automatic,
        "access_medium": features.Access_Medium,
        "error_code": features.Error_Code,
        "num_connection_failure": features.Transaction_num_connection_failure,
        "another_person_account": bool(features.Another_Person_Account),
        "initial_balance": features.Account_initial_balance,
        "balance": features.Account_balance,
        "remaining_amount_daily_limit_exceeded": (
            features.Account_remaining_amount_daily_limit_exceeded
        ),
        "operating_system": features.Operating_System,
        "location": features.Location,
        "location_lat": latitude,
        "location_lon": longitude,
        "rooting_jailbreak_indicator": bool(
            features.Customer_rooting_jailbreak_indicator
        ),
        "mobile_roaming_indicator": bool(
            features.Customer_mobile_roaming_indicator
        ),
        "vpn_indicator": bool(features.Customer_VPN_Indicator),
        "flag_terminal_malicious_behavior_1": bool(
            features.Customer_flag_terminal_malicious_behavior_1
        ),
        "flag_terminal_malicious_behavior_2": bool(
            features.Customer_flag_terminal_malicious_behavior_2
        ),
        "flag_terminal_malicious_behavior_3": bool(
            features.Customer_flag_terminal_malicious_behavior_3
        ),
        "flag_terminal_malicious_behavior_5": bool(
            features.Customer_flag_terminal_malicious_behavior_5
        ),
        "flag_terminal_malicious_behavior_6": bool(
            features.Customer_flag_terminal_malicious_behavior_6
        ),
    }


def build_derived_features_fields(
    features: MLTransactionFeatures,
) -> dict[str, object]:
    """derived_features 테이블이 보관하는 파생 피처를 추출한다."""

    return {
        "distance": features.Distance,
        "time_difference": parse_time_difference(features.time_difference),
        "one_month_max_amount": features.Account_one_month_max_amount,
        "one_month_std_dev": features.Account_one_month_std_dev,
        "dawn_one_month_max_amount": features.Account_dawn_one_month_max_amount,
        "dawn_one_month_std_dev": features.Account_dawn_one_month_std_dev,
        "unused_terminal_status": bool(features.Unused_terminal_status),
        "unused_account_status": bool(features.Unused_account_status),
        "flag_deposit_more_than_tenmillion": bool(
            features.Flag_deposit_more_than_tenMillion
        ),
        "number_of_transaction_with_the_account": (
            features.Number_of_transaction_with_the_account
        ),
        "transaction_history_with_the_account": (
            features.Transaction_history_with_the_account
        ),
        "last_atm_transaction_datetime": (
            features.Last_atm_transaction_datetime
        ),
        "last_bank_branch_transaction_datetime": (
            features.Last_bank_branch_transaction_datetime
        ),
        "flag_change_of_authentication_1": bool(
            features.Customer_flag_change_of_authentication_1
        ),
        "flag_change_of_authentication_2": bool(
            features.Customer_flag_change_of_authentication_2
        ),
        "flag_change_of_authentication_3": bool(
            features.Customer_flag_change_of_authentication_3
        ),
        "flag_change_of_authentication_4": bool(
            features.Customer_flag_change_of_authentication_4
        ),
        "inquiry_atm_limit": bool(features.Customer_inquery_atm_limit),
        "increase_atm_limit": bool(features.Customer_increase_atm_limit),
        "release_suspension": bool(features.Account_release_suspension),
        "transaction_resumed_date": features.Transaction_resumed_date,
        "recipient_account_suspend_status": bool(
            features.Recipient_account_suspend_status
        ),
        "first_time_ios_by_vulnerable_user": bool(
            features.First_time_iOS_by_vulnerable_user
        ),
    }


def assemble_ml_features(
    *,
    customer: Customer,
    source_account: Account,
    transaction: Transaction,
    derived: DerivedFeatures,
) -> MLTransactionFeatures:
    """네 테이블의 행에서 ML 54개 Feature 계약 객체를 복원한다.

    한도·오픈뱅킹 등 accounts에서 읽는 값은 ERD가 불변으로 규정한 컬럼만
    사용한다. 가변으로 표시된 잔액 계열은 transactions의 스냅샷을 읽는다.
    """

    if source_account.amount_daily_limit is None:
        raise FeatureAssemblyError(
            "출금 계좌에 amount_daily_limit이 없어 Feature를 복원할 수 없습니다: "
            f"{source_account.account_id}"
        )
    if source_account.account_type is None:
        raise FeatureAssemblyError(
            "출금 계좌에 account_type이 없어 Feature를 복원할 수 없습니다: "
            f"{source_account.account_id}"
        )
    if source_account.creation_datetime is None:
        raise FeatureAssemblyError(
            "출금 계좌에 creation_datetime이 없어 Feature를 복원할 수 없습니다: "
            f"{source_account.account_id}"
        )

    return MLTransactionFeatures(
        Customer_Birthyear=customer.birthyear,
        Customer_Gender=customer.gender,
        Customer_registration_datetime=customer.registration_datetime,
        Customer_credit_rating=customer.credit_rating,
        Customer_flag_change_of_authentication_1=int(
            derived.flag_change_of_authentication_1
        ),
        Customer_flag_change_of_authentication_2=int(
            derived.flag_change_of_authentication_2
        ),
        Customer_flag_change_of_authentication_3=int(
            derived.flag_change_of_authentication_3
        ),
        Customer_flag_change_of_authentication_4=int(
            derived.flag_change_of_authentication_4
        ),
        Customer_rooting_jailbreak_indicator=int(
            transaction.rooting_jailbreak_indicator
        ),
        Customer_mobile_roaming_indicator=int(
            transaction.mobile_roaming_indicator
        ),
        Customer_VPN_Indicator=int(transaction.vpn_indicator),
        Customer_loan_type=customer.loan_type,
        Customer_flag_terminal_malicious_behavior_1=int(
            transaction.flag_terminal_malicious_behavior_1
        ),
        Customer_flag_terminal_malicious_behavior_2=int(
            transaction.flag_terminal_malicious_behavior_2
        ),
        Customer_flag_terminal_malicious_behavior_3=int(
            transaction.flag_terminal_malicious_behavior_3
        ),
        Customer_flag_terminal_malicious_behavior_5=int(
            transaction.flag_terminal_malicious_behavior_5
        ),
        Customer_flag_terminal_malicious_behavior_6=int(
            transaction.flag_terminal_malicious_behavior_6
        ),
        Customer_inquery_atm_limit=int(derived.inquiry_atm_limit),
        Customer_increase_atm_limit=int(derived.increase_atm_limit),
        Account_account_type=source_account.account_type,
        Account_creation_datetime=source_account.creation_datetime,
        Account_initial_balance=transaction.initial_balance,
        Account_balance=transaction.balance,
        Account_indicator_release_limit_excess=int(
            bool(source_account.indicator_release_limit_excess)
        ),
        Account_amount_daily_limit=source_account.amount_daily_limit,
        Account_indicator_Openbanking=int(
            bool(source_account.indicator_openbanking)
        ),
        Account_remaining_amount_daily_limit_exceeded=(
            transaction.remaining_amount_daily_limit_exceeded
        ),
        Account_release_suspension=int(derived.release_suspension),
        Account_one_month_max_amount=derived.one_month_max_amount,
        Account_one_month_std_dev=derived.one_month_std_dev,
        Account_dawn_one_month_max_amount=derived.dawn_one_month_max_amount,
        Account_dawn_one_month_std_dev=derived.dawn_one_month_std_dev,
        Transaction_Datetime=transaction.transaction_datetime,
        Transaction_Amount=transaction.transaction_amount,
        Channel=transaction.channel,
        Operating_System=transaction.operating_system,
        Error_Code=transaction.error_code,
        Type_General_Automatic=transaction.type_general_automatic,
        Access_Medium=transaction.access_medium,
        Location=transaction.location,
        Transaction_num_connection_failure=transaction.num_connection_failure,
        Another_Person_Account=int(transaction.another_person_account),
        Distance=derived.distance,
        time_difference=format_time_difference(derived.time_difference),
        Unused_terminal_status=int(derived.unused_terminal_status),
        Last_atm_transaction_datetime=derived.last_atm_transaction_datetime,
        Last_bank_branch_transaction_datetime=(
            derived.last_bank_branch_transaction_datetime
        ),
        Flag_deposit_more_than_tenMillion=int(
            derived.flag_deposit_more_than_tenmillion
        ),
        Unused_account_status=int(derived.unused_account_status),
        Recipient_account_suspend_status=int(
            derived.recipient_account_suspend_status
        ),
        Number_of_transaction_with_the_account=(
            derived.number_of_transaction_with_the_account
        ),
        Transaction_history_with_the_account=(
            derived.transaction_history_with_the_account
        ),
        First_time_iOS_by_vulnerable_user=int(
            derived.first_time_ios_by_vulnerable_user
        ),
        Transaction_resumed_date=derived.transaction_resumed_date,
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
