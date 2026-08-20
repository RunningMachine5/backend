"""ML 담당자가 정의한 raw51 Feature와 정규화 테이블 사이의 변환 함수."""

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.dto.ml_features import MLTransactionFeatures


def build_customer_fields(features: MLTransactionFeatures) -> dict[str, object]:
    """raw51의 고객 값을 customers 컬럼으로 옮긴다."""

    return {
        "birth_date": features.customer_birth_date,
        "gender": features.customer_gender,
        "registration_datetime": features.customer_registration_datetime,
        "credit_rating": features.customer_credit_rating,
        "loan_type": features.customer_loan_type,
    }


def build_account_fields(features: MLTransactionFeatures) -> dict[str, object]:
    """raw51의 출금 계좌 값을 accounts 컬럼으로 옮긴다."""

    return {
        "account_type": features.account_account_type,
        "creation_datetime": features.account_creation_datetime,
        "amount_daily_limit": features.account_amount_daily_limit,
        "indicator_openbanking": features.account_indicator_openbanking,
        "current_balance": features.account_balance,
    }


def build_transaction_fields(
    features: MLTransactionFeatures,
) -> dict[str, object]:
    """raw51에서 거래 시점 원본과 계좌 스냅샷을 만든다."""

    return {
        "transaction_datetime": features.transaction_datetime,
        "transaction_amount": features.transaction_amount,
        "channel": features.channel.lower(),
        "type_general_automatic": features.type_general_automatic.lower(),
        "access_medium": (
            features.access_medium.lower() if features.access_medium else None
        ),
        "error_code": None,
        "num_connection_failure": features.transaction_num_connection_failure,
        "initial_balance": features.account_initial_balance,
        "balance": features.account_balance,
        "operating_system": features.operating_system,
        "ip_address": None,
        "mac_address": None,
        "location_lat": None,
        "location_lon": None,
        "rooting_jailbreak_indicator": (
            features.customer_rooting_jailbreak_indicator
        ),
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
    """담당자 Feature 서비스가 계산한 값을 derived_features에 저장한다."""

    return {
        "remaining_amount_daily_limit": (
            features.account_remaining_amount_daily_limit_exceeded
        ),
        "distance": features.distance,
        "time_difference": features.time_difference,
        "one_month_max_amount": features.account_one_month_max_amount,
        "one_month_std_dev": features.account_one_month_std_dev,
        "dawn_one_month_max_amount": features.account_dawn_one_month_max_amount,
        "dawn_one_month_std_dev": features.account_dawn_one_month_std_dev,
        "another_person_account": features.another_person_account,
        "unused_terminal_status": features.unused_terminal_status,
        "unused_account_status": features.unused_account_status,
        "transaction_history_with_the_account": (
            features.transaction_history_with_the_account
        ),
        "flag_deposit_more_than_ten_million": (
            features.flag_deposit_more_than_ten_million
        ),
        "number_of_transaction_with_the_account": (
            features.number_of_transaction_with_the_account
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
        "inquery_atm_limit": features.customer_inquery_atm_limit,
        "increase_atm_limit": features.customer_increase_atm_limit,
        "indicator_release_limit_excess": (
            features.account_indicator_release_limit_excess
        ),
        "recipient_release_suspension": features.recipient_release_suspension,
        "recipient_transaction_resumed_date": (
            features.recipient_transaction_resumed_date
        ),
        "recipient_account_suspend_status": (
            features.recipient_account_suspend_status
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
    """저장된 고객·계좌·거래·파생값에서 같은 raw51을 복원한다."""

    return MLTransactionFeatures(
        customer_birth_date=customer.birth_date,
        customer_gender=customer.gender,
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
        customer_rooting_jailbreak_indicator=transaction.rooting_jailbreak_indicator,
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
        customer_inquery_atm_limit=derived.inquery_atm_limit,
        customer_increase_atm_limit=derived.increase_atm_limit,
        account_account_type=source_account.account_type,
        account_creation_datetime=source_account.creation_datetime,
        account_initial_balance=transaction.initial_balance,
        # 거절 거래는 DB 잔액을 출금 전 값으로 되돌린다. ML이 처음 판단할 때
        # 사용한 출금 시도 후 잔액은 담당자 계산식으로 다시 만든다.
        account_balance=(
            transaction.initial_balance - transaction.transaction_amount
        ),
        account_indicator_release_limit_excess=(
            derived.indicator_release_limit_excess
        ),
        account_amount_daily_limit=source_account.amount_daily_limit,
        account_indicator_openbanking=source_account.indicator_openbanking,
        account_remaining_amount_daily_limit_exceeded=(
            derived.remaining_amount_daily_limit
        ),
        recipient_release_suspension=derived.recipient_release_suspension,
        account_one_month_max_amount=derived.one_month_max_amount,
        account_one_month_std_dev=derived.one_month_std_dev,
        account_dawn_one_month_max_amount=derived.dawn_one_month_max_amount,
        account_dawn_one_month_std_dev=derived.dawn_one_month_std_dev,
        transaction_datetime=transaction.transaction_datetime,
        transaction_amount=transaction.transaction_amount,
        channel=transaction.channel,
        operating_system=transaction.operating_system,
        type_general_automatic=transaction.type_general_automatic,
        access_medium=transaction.access_medium,
        transaction_num_connection_failure=transaction.num_connection_failure,
        another_person_account=derived.another_person_account,
        distance=derived.distance,
        time_difference=derived.time_difference,
        unused_terminal_status=derived.unused_terminal_status,
        last_atm_transaction_datetime=derived.last_atm_transaction_datetime,
        last_bank_branch_transaction_datetime=(
            derived.last_bank_branch_transaction_datetime
        ),
        flag_deposit_more_than_ten_million=(
            derived.flag_deposit_more_than_ten_million
        ),
        unused_account_status=derived.unused_account_status,
        recipient_account_suspend_status=(
            derived.recipient_account_suspend_status
        ),
        number_of_transaction_with_the_account=(
            derived.number_of_transaction_with_the_account
        ),
        transaction_history_with_the_account=(
            derived.transaction_history_with_the_account
        ),
        recipient_transaction_resumed_date=(
            derived.recipient_transaction_resumed_date
        ),
    )


__all__ = [
    "assemble_ml_features",
    "build_account_fields",
    "build_customer_fields",
    "build_derived_features_fields",
    "build_transaction_fields",
]
