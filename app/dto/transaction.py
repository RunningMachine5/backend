from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, field_validator, model_validator
from pydantic import Field as PydanticField
from sqlmodel import SQLModel

from app.dto.ml_prediction import (
    MAC_ADDRESS_PATTERN,
    RAW_TRANSACTION_FEATURE_COLUMNS,
    MLTransactionFeatures,
)


class TransactionCreateDTO(SQLModel):
    """ML 담당자의 raw64 한 행을 받는 거래 탐지 요청.

    정식 HTTP/CSV 계약은 ``transaction_id + raw59 + 학습 메타데이터 4개``인
    flat snake_case다. 프로그램 호출 편의를 위해 raw59만 ``raw_features``에
    중첩한 형태도 함께 허용한다.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = PydanticField(min_length=1, max_length=64)
    customer_id: str = PydanticField(min_length=1, max_length=64)
    customer_identification_number: str = PydanticField(
        min_length=1,
        max_length=255,
    )
    balance_drain_ratio: float | None = PydanticField(default=None, ge=0)
    is_fraud: bool | None = None
    raw_features: MLTransactionFeatures

    @field_validator("transaction_id", mode="before")
    @classmethod
    def normalize_transaction_id(cls, value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            # Pydantic v2 does not wrap TypeError raised by validators.
            raise ValueError(  # noqa: TRY004
                "transaction_id must be a non-empty string or integer"
            )
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("transaction_id must not be empty")
        return normalized

    @field_validator("is_fraud", mode="before")
    @classmethod
    def normalize_fraud_label(cls, value: object) -> bool | None:
        if value is None or value == "":
            return None
        if value in (0, 1, False, True, "0", "1"):
            return str(value).lower() in {"1", "true"}
        raise ValueError("is_fraud must be 0, 1, true, false, or null")

    @model_validator(mode="before")
    @classmethod
    def split_flat_raw64_row(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "raw_features" in value:
            return value

        feature_keys = set(RAW_TRANSACTION_FEATURE_COLUMNS)
        # train1.csv 한 컬럼의 알려진 오타도 입력에서만 허용한다.
        feature_keys.add("flag_deposit_more_than_tenmillion")
        raw_features = {key: item for key, item in value.items() if key in feature_keys}
        remaining = {
            key: item for key, item in value.items() if key not in feature_keys
        }
        remaining["raw_features"] = raw_features
        return remaining

    @property
    def customer_personal_identifier(self) -> str:
        return self.raw_features.customer_name

    @property
    def source_account_number(self) -> str:
        return self.raw_features.account_account_number

    @property
    def recipient_account_number(self) -> str:
        return self.raw_features.recipient_account_number

    @property
    def ip_address(self) -> str | None:
        return self.raw_features.ip_address

    @property
    def mac_address(self) -> str | None:
        return self.raw_features.mac_address

    @property
    def confirmed_is_fraud(self) -> bool | None:
        """기존 저장소 내부 명칭과의 호환 프로퍼티."""

        return self.is_fraud


class TransactionResponseDTO(SQLModel):
    """저장된 거래와 ML·룰 탐지 결과 응답."""

    transaction_id: str
    customer_id: str
    source_account_id: str
    recipient_account_id: str | None
    transaction_datetime: datetime
    transaction_amount: int
    channel: str
    location: str
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
    model_config = ConfigDict(extra="forbid")

    confirmed_is_fraud: bool


class TransactionLabelResponseDTO(SQLModel):
    transaction_id: str
    confirmed_is_fraud: bool
    labeled_at: datetime


@dataclass(frozen=True)
class TransactionDTO:
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
