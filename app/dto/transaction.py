import re
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from typing import Any

from pydantic import (
    AliasChoices,
    ConfigDict,
    StrictBool,
    field_validator,
    model_validator,
)
from sqlmodel import Field, SQLModel

from app.dto.ml_prediction import (
    RAW_TRANSACTION_FEATURE_COLUMNS,
    MLTransactionFeatures,
)

MAC_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class TransactionCreateDTO(SQLModel):
    """CSV 한 행 또는 정규화된 JSON으로 받는 거래 탐지 요청 DTO."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("transaction_id", "ID"),
    )
    customer_id: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("customer_id", "Customer_ID"),
    )
    customer_personal_identifier: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices(
            "customer_personal_identifier",
            "Customer_personal_identifier",
        ),
    )
    customer_identification_number: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices(
            "customer_identification_number",
            "Customer_identification_number",
        ),
    )
    source_account_number: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices(
            "source_account_number",
            "Account_account_number",
        ),
    )
    recipient_account_number: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices(
            "recipient_account_number",
            "Recipient_Account_Number",
        ),
    )
    ip_address: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ip_address", "IP_Address"),
    )
    mac_address: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mac_address", "MAC_Address"),
    )
    confirmed_is_fraud: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("confirmed_is_fraud", "Is_Fraud"),
    )
    raw_features: MLTransactionFeatures = Field(
        validation_alias=AliasChoices("raw_features", "raw_data"),
    )

    @field_validator("ip_address", mode="before")
    @classmethod
    def validate_ip_address(cls, value: object) -> str | None:
        """PostgreSQL INET에 도달하기 전에 주소를 검증·정규화한다."""

        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return ip_address(str(value).strip()).compressed
        except ValueError as exc:
            raise ValueError("IP_Address는 올바른 IPv4 또는 IPv6여야 합니다.") from exc

    @field_validator("mac_address", mode="before")
    @classmethod
    def validate_mac_address(cls, value: object) -> str | None:
        """PostgreSQL MACADDR가 받는 6옥텟 주소를 표준 표기로 정규화한다."""

        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        normalized = str(value).strip()
        if MAC_ADDRESS_PATTERN.fullmatch(normalized) is None:
            raise ValueError("MAC_Address는 6옥텟 MAC 주소여야 합니다.")
        return normalized.replace("-", ":").lower()

    @model_validator(mode="before")
    @classmethod
    def split_flat_csv_row(cls, value: Any) -> Any:
        """CSV 컬럼이 평평하게 들어오면 54개 ML Feature를 자동 분리한다."""

        if not isinstance(value, dict):
            return value
        if "raw_features" in value or "raw_data" in value:
            return value

        feature_keys = set(RAW_TRANSACTION_FEATURE_COLUMNS)
        feature_keys.update(MLTransactionFeatures.model_fields)
        raw_features = {key: item for key, item in value.items() if key in feature_keys}
        remaining = {
            key: item for key, item in value.items() if key not in feature_keys
        }
        remaining["raw_features"] = raw_features
        return remaining


class TransactionResponseDTO(SQLModel):
    """저장된 거래와 ML·룰 탐지 결과를 반환하는 응답 DTO."""

    transaction_id: str
    customer_id: str
    source_account_id: str
    recipient_account_id: str | None
    transaction_datetime: datetime
    transaction_amount: int
    channel: str
    location: str
    # 평탄화된 컬럼에서 다시 조립한 ML 54개 Feature. 파생 피처 행이 없어
    # 복원할 수 없으면 None이다.
    raw_features: dict[str, Any] | None
    prediction_status: str
    ml_is_fraud: bool | None
    fraud_probability: float | None
    model_name: str | None
    model_version: str | None
    latency_ms: int | None
    created_at: datetime
    rule_scores: dict[str, float] | None = None
    rule_set_id: int | None = None
    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None


class TransactionLabelUpdateDTO(SQLModel):
    """담당자가 확정한 거래의 이진 정답 라벨."""

    model_config = ConfigDict(extra="forbid")

    confirmed_is_fraud: StrictBool


class TransactionLabelResponseDTO(SQLModel):
    """저장된 거래 정답 라벨 응답."""

    transaction_id: str
    confirmed_is_fraud: bool
    labeled_at: datetime


@dataclass(frozen=True)
class TransactionDTO:
    """구형 모니터링 에이전트 스켈레톤과의 임시 호환 DTO."""

    user_id: str
    user_name: str
    email: str
    transaction_time: str
    amount: int
    user_amount_std_dev: float
    payment_method: str
    merchant_category: str


@dataclass(frozen=True)
class TransactionFeaturesDTO:
    """구형 모니터링 에이전트 스켈레톤과의 임시 호환 결과 DTO."""

    user_id: str
    is_fraud: bool
    high_relevance_feature: dict
    fraud_probability: float


__all__ = [
    "MAC_ADDRESS_PATTERN",
    "TransactionCreateDTO",
    "TransactionDTO",
    "TransactionFeaturesDTO",
    "TransactionLabelResponseDTO",
    "TransactionLabelUpdateDTO",
    "TransactionResponseDTO",
]
