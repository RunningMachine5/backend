"""Backend가 ML Serving에 전달하는 전처리 전 거래 Feature 계약."""

import re
from datetime import datetime, timedelta, date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BinaryFlag = Literal[0, 1]
LOCATION_PATTERN = re.compile(
    r"(?P<latitude>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(?P<longitude>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)


class MLTransactionFeatures(BaseModel):
    """
    ML서버로 보낼 피쳐.
    날짜 성분 추출, One-Hot Encoding과 학습 Schema 정렬은 ML 서버가 담당한다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    customer_birth_date: date
    customer_gender: str
    customer_registration_datetime: datetime
    customer_credit_rating: int
    customer_flag_change_of_authentication_1: bool
    customer_flag_change_of_authentication_2: bool
    customer_flag_change_of_authentication_3: bool
    customer_flag_change_of_authentication_4: bool
    customer_rooting_jailbreak_indicator: bool
    customer_mobile_roaming_indicator: bool
    customer_vpn_indicator: bool
    customer_loan_type: str
    customer_flag_terminal_malicious_behavior_1: bool
    customer_flag_terminal_malicious_behavior_2: bool
    customer_flag_terminal_malicious_behavior_3: bool
    customer_flag_terminal_malicious_behavior_5: bool
    customer_flag_terminal_malicious_behavior_6: bool
    customer_inquery_atm_limit: bool
    customer_increase_atm_limit: bool
    account_account_type: str
    account_creation_datetime: datetime
    account_initial_balance: int
    account_balance: int
    account_amount_daily_limit: int
    account_indicator_release_limit_excess: bool
    account_remaining_amount_daily_limit_exceeded: int
    account_indicator_openbanking: bool
    recipient_release_suspension: bool
    account_one_month_max_amount: int
    account_one_month_std_dev: float
    account_dawn_one_month_max_amount: int
    account_dawn_one_month_std_dev: float
    transaction_datetime: datetime
    transaction_amount: int
    channel: str
    operating_system: str | None = None
    type_general_automatic: str
    access_medium: str | None = None
    transaction_num_connection_failure: int
    another_person_account: bool
    distance: float
    time_difference: timedelta
    unused_terminal_status: bool
    last_atm_transaction_datetime: datetime | None = None
    last_bank_branch_transaction_datetime: datetime | None = None
    flag_deposit_more_than_ten_million: bool
    unused_account_status: bool
    recipient_account_suspend_status: bool
    number_of_transaction_with_the_account: int = 0
    transaction_history_with_the_account: int
    recipient_transaction_resumed_date: datetime | None = None

RAW_TRANSACTION_FEATURE_COLUMNS = tuple(
    field.serialization_alias or field.alias or name
    for name, field in MLTransactionFeatures.model_fields.items()
)


class DerivedFeaturesCreateDTO(BaseModel):

    # TXN 현재
    remaining_amount_daily_limit: int

    # TXN 직전 1건
    distance: float
    time_difference: timedelta

    # TXN 1개월 (전체 / 새벽)
    one_month_max_amount: int
    one_month_std_dev: float
    dawn_one_month_max_amount: int
    dawn_one_month_std_dev: float

    # 기존 거래 전체
    another_person_account: bool
    unused_terminal_status: bool
    unused_account_status: bool
    transaction_history_with_the_account: int

    # TXN 7일
    flag_deposit_more_than_ten_million: bool

    # TXN 3시간 / 누적
    number_of_transaction_with_the_account: int

    # TXN 채널 이력
    last_atm_transaction_datetime: datetime | None = None
    last_bank_branch_transaction_datetime: datetime | None = None

    # EVENT 90일
    flag_change_of_authentication_1: bool
    flag_change_of_authentication_2: bool
    flag_change_of_authentication_3: bool
    flag_change_of_authentication_4: bool

    # EVENT 7일
    inquery_atm_limit: bool
    increase_atm_limit: bool
    indicator_release_limit_excess: bool | None = None

    # EVENT 30일
    recipient_release_suspension: bool
    recipient_transaction_resumed_date: datetime | None = None

    # 조인 결과 스냅샷(현재)
    recipient_account_suspend_status: bool

__all__ = [
    "RAW_TRANSACTION_FEATURE_COLUMNS",
    "MLTransactionFeatures",
    "DerivedFeaturesCreateDTO",
]
