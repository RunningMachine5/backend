"""Backend와 ML Serving이 공유하는 전처리 전 raw59 거래 계약."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

BinaryFlag = Literal[0, 1]
CHANNELS = frozenset({"mobile", "internet", "atm", "others"})
OPERATING_SYSTEMS = frozenset({"android", "ios", "windows", "macos", "linux", "others"})
LOCATION_PATTERN = re.compile(
    r"(?P<latitude>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(?P<longitude>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)
MAC_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_SINGLE_DIGIT_HOUR_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<hour>\d)(?P<rest>:\d{2}(?::\d{2}(?:\.\d+)?)?)$"
)


class MLTransactionFeatures(BaseModel):
    """ML 담당자의 flat 60 DTO에서 ``transaction_id``를 제외한 59개 필드.

    이 객체는 One-hot Encoding이나 금액 부호 변환을 하지 않는다. Backend는
    검증·정규화한 원본값을 저장하고 ML Serving이 동일한 raw59를 model80으로
    전처리한다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    customer_birth_date: datetime
    customer_gender: Literal["male", "female"]
    customer_name: str = Field(min_length=1, max_length=255)
    customer_registration_datetime: datetime
    customer_credit_rating: int = Field(ge=1, le=9)
    customer_flag_change_of_authentication_1: bool
    customer_flag_change_of_authentication_2: bool
    customer_flag_change_of_authentication_3: bool
    customer_flag_change_of_authentication_4: bool
    customer_rooting_jailbreak_indicator: bool
    customer_mobile_roaming_indicator: bool
    customer_vpn_indicator: bool
    customer_loan_type: Literal["a", "b", "c", "d", "e"]
    customer_flag_terminal_malicious_behavior_1: bool
    customer_flag_terminal_malicious_behavior_2: bool
    customer_flag_terminal_malicious_behavior_3: bool
    customer_flag_terminal_malicious_behavior_5: bool
    customer_flag_terminal_malicious_behavior_6: bool
    customer_inquery_atm_limit: bool
    customer_increase_atm_limit: bool

    account_account_number: str = Field(min_length=1, max_length=255)
    account_account_type: Literal["a", "b", "c", "d"]
    account_creation_datetime: datetime
    account_initial_balance: int = Field(ge=0)
    # train1.csv에서 음수 3,481건이 존재하는 거래 후 잔액이다.
    account_balance: int
    account_indicator_release_limit_excess: BinaryFlag
    account_amount_daily_limit: int = Field(ge=0)
    account_indicator_openbanking: bool
    account_remaining_amount_daily_limit_exceeded: int = Field(ge=0)
    # ML 담당자 계약의 철자를 그대로 유지한다.
    account_release_suspention: bool
    account_one_month_max_amount: int = Field(ge=0)
    account_one_month_std_dev: float = Field(ge=0)
    account_dawn_one_month_max_amount: int = Field(ge=0)
    account_dawn_one_month_std_dev: float = Field(ge=0)

    transaction_datetime: datetime
    # train1.csv와 ML 전처리는 출금액의 절댓값인 양수를 그대로 사용한다.
    transaction_amount: int = Field(gt=0)
    channel: str
    operating_system: str | None
    error_code: str = Field(min_length=1, max_length=64)
    type_general_automatic: Literal["general", "automatic"]
    ip_address: str | None
    mac_address: str | None
    access_medium: Literal["a", "b", "c", "d", "e", "f", "g", "h"]
    location: str = Field(min_length=1)
    recipient_account_number: str = Field(min_length=1, max_length=255)
    transaction_num_connection_failure: int = Field(ge=0)
    another_person_account: bool
    distance: float = Field(ge=0)
    time_difference: timedelta
    unused_terminal_status: bool
    last_atm_transaction_datetime: datetime | None
    last_bank_branch_transaction_datetime: datetime | None
    flag_deposit_more_than_ten_million: bool = Field(
        validation_alias=AliasChoices(
            "flag_deposit_more_than_ten_million",
            "flag_deposit_more_than_tenmillion",
        )
    )
    unused_account_status: bool
    recipient_account_suspend_status: bool
    number_of_transaction_with_the_account: int = Field(ge=0)
    transaction_history_with_the_account: int = Field(ge=0)
    first_time_ios_by_vulnerable_user: bool
    transaction_resumed_date: datetime | None

    @field_validator(
        "customer_birth_date",
        "customer_registration_datetime",
        "account_creation_datetime",
        "transaction_datetime",
        "last_atm_transaction_datetime",
        "last_bank_branch_transaction_datetime",
        "transaction_resumed_date",
        mode="before",
    )
    @classmethod
    def normalize_train1_datetime(cls, value: object) -> object:
        """train1의 한 자리 시각(``YYYY-MM-DD H:MM``)을 ISO로 맞춘다."""

        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return None
        match = _SINGLE_DIGIT_HOUR_PATTERN.fullmatch(normalized)
        if match is None:
            return normalized
        return f"{match.group('date')}T0{match.group('hour')}{match.group('rest')}"

    @field_validator(
        "account_account_number", "recipient_account_number", mode="before"
    )
    @classmethod
    def normalize_account_number(cls, value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            # Pydantic v2 does not wrap TypeError raised by validators.
            raise ValueError(  # noqa: TRY004
                "account number must be a non-empty string or integer"
            )
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("account number must not be empty")
        return normalized

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if normalized not in CHANNELS:
            raise ValueError(f"channel must be one of {sorted(CHANNELS)}")
        return normalized

    @field_validator("operating_system", mode="before")
    @classmethod
    def normalize_operating_system(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        normalized = str(value).strip().lower()
        if normalized not in OPERATING_SYSTEMS:
            raise ValueError(
                f"operating_system must be one of {sorted(OPERATING_SYSTEMS)}"
            )
        return normalized

    @field_validator("ip_address", mode="before")
    @classmethod
    def normalize_ip_address(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return ip_address(str(value).strip()).compressed
        except ValueError as exc:
            raise ValueError("ip_address must be a valid IPv4 or IPv6 address") from exc

    @field_validator("mac_address", mode="before")
    @classmethod
    def normalize_mac_address(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        normalized = str(value).strip()
        if MAC_ADDRESS_PATTERN.fullmatch(normalized) is None:
            raise ValueError("mac_address must be a six-octet MAC address")
        return normalized.replace("-", ":").lower()

    @field_validator(
        "account_remaining_amount_daily_limit_exceeded",
        mode="before",
    )
    @classmethod
    def reject_boolean_remaining_limit(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError(  # noqa: TRY004
                "account_remaining_amount_daily_limit_exceeded is an amount, "
                "not a boolean"
            )
        return value

    @field_serializer("time_difference", when_used="json")
    def serialize_time_difference(self, value: timedelta) -> float:
        """ML 정식 요청처럼 시간 간격을 초 단위 숫자로 직렬화한다."""

        return value.total_seconds()

    @model_validator(mode="after")
    def validate_event_time_order(self) -> Self:
        if self.customer_birth_date.date() > self.transaction_datetime.date():
            raise ValueError(
                "customer_birth_date must not be after transaction_datetime"
            )
        return self


RAW_TRANSACTION_FEATURE_COLUMNS = tuple(MLTransactionFeatures.model_fields)

if len(RAW_TRANSACTION_FEATURE_COLUMNS) != 59:  # pragma: no cover
    raise RuntimeError("MLTransactionFeatures must contain exactly 59 fields")


__all__ = [
    "CHANNELS",
    "LOCATION_PATTERN",
    "MAC_ADDRESS_PATTERN",
    "OPERATING_SYSTEMS",
    "RAW_TRANSACTION_FEATURE_COLUMNS",
    "MLTransactionFeatures",
]
