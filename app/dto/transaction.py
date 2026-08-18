from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.data.model.transaction import TransactionStatus


class TransactionRequestDTO(BaseModel):
    """
    외부 클라이언트가 보내는 거래 원시 데이터.
    계좌 정보와 단말기에서 감지할 수 있는 정보들이 들어온다.
    """
    model_config = ConfigDict(extra="forbid")

    # ATM·지점 거래는 고객 식별자가 전달되지 않을 수 있다.
    customer_id: int | None = Field(default=None)
    source_account_number: str = Field(min_length=8, max_length=255)
    recipient_account_number: str = Field(
        min_length=8,
        max_length=255,
    )
    transaction_datetime: datetime
    transaction_amount: int

    channel: str
    type_general_automatic: str
    access_medium: str | None = None
    num_connection_failure: int = Field(ge=0)

    operating_system: str | None = Field(default=None, max_length=32)
    ip_address: str | None = None
    mac_address: str | None = None

    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)

    rooting_jailbreak_indicator: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "rooting_jailbreak_indicator",
            "customer_rooting_jailbreak_indicator",
        ),
    )
    mobile_roaming_indicator: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "mobile_roaming_indicator",
            "customer_mobile_roaming_indicator",
        ),
    )
    vpn_indicator: bool = Field(
        default=False,
        validation_alias=AliasChoices("vpn_indicator", "customer_vpn_indicator"),
    )
    flag_terminal_malicious_behavior_1: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "flag_terminal_malicious_behavior_1",
            "customer_flag_terminal_malicious_behavior_1",
        ),
    )
    flag_terminal_malicious_behavior_2: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "flag_terminal_malicious_behavior_2",
            "customer_flag_terminal_malicious_behavior_2",
        ),
    )
    flag_terminal_malicious_behavior_3: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "flag_terminal_malicious_behavior_3",
            "customer_flag_terminal_malicious_behavior_3",
        ),
    )
    flag_terminal_malicious_behavior_5: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "flag_terminal_malicious_behavior_5",
            "customer_flag_terminal_malicious_behavior_5",
        ),
    )
    flag_terminal_malicious_behavior_6: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "flag_terminal_malicious_behavior_6",
            "customer_flag_terminal_malicious_behavior_6",
        ),
    )

class TransactionResponseDTO(BaseModel):
    """저장된 거래와 ML·룰 탐지 결과를 반환하는 응답 DTO."""
    transaction_id: int = Field(strict=True, gt=0)
    prediction_status: Literal["COMPLETED", "FAILED", "DECLINED"]
    predict_result: bool | None = None
    predict_proba: float | None = Field(default=None, ge=0, le=1)
    rule_set_id: int | None = None
    rule_scores: dict[str, float] | None = None
    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None
    created_at: datetime
    message: str | None = None

#doo
class TransactionCreateDTO(BaseModel):
    customer_id: int | None = None

    source_account_number: str
    recipient_account_number: str
    transaction_datetime: datetime
    transaction_amount: int

    channel: str
    type_general_automatic: str
    access_medium: str | None = None
    num_connection_failure: int = 0

    # 거래 시점 계좌 상태 스냅샷
    initial_balance: int | None = None
    balance: int | None = None

    # 단말·접속 환경
    operating_system: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None

    rooting_jailbreak_indicator: bool
    mobile_roaming_indicator: bool
    vpn_indicator: bool
    flag_terminal_malicious_behavior_1: bool
    flag_terminal_malicious_behavior_2: bool
    flag_terminal_malicious_behavior_3: bool
    flag_terminal_malicious_behavior_5: bool
    flag_terminal_malicious_behavior_6: bool

    transaction_status: TransactionStatus | None = TransactionStatus.APPROVED
    error_code: str | None = None

class TransactionLabelUpdateDTO(BaseModel):
    """담당자가 확정한 거래의 이진 정답 라벨."""

    model_config = ConfigDict(extra="forbid")

    confirmed_is_fraud: bool


class TransactionLabelResponseDTO(BaseModel):
    """저장된 거래 정답 라벨 응답."""

    transaction_id: int = Field(strict=True, gt=0)
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
    "TransactionLabelResponseDTO",
    "TransactionLabelUpdateDTO",
    "TransactionRequestDTO",
    "TransactionResponseDTO",
]
