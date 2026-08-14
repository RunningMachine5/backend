import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAC_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

class TransactionRequestDTO(BaseModel):
    """
    외부 클라이언트가 보내는 거래 원시 데이터.
    계좌 정보와 단말기에서 감지할 수 있는 정보들이 들어온다.
    """
    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = Field(default=None, min_length=1, max_length=64) # atm, 지점 거래의 경우 None.
    source_account_number: str = Field(min_length=8, max_length=32)
    recipient_account_number: str | None = Field(default=None, min_length=8, max_length=32) # atm 입금의 경우엔 상대 계좌 없을 수 있음.
    transaction_datetime: datetime
    transaction_amount: int

    channel: str = Field(min_length=1, max_length=32)
    type_general_automatic: str = Field(min_length=1, max_length=16)
    access_medium: str | None = Field(default=None, max_length=8)
    num_connection_failure: int = Field(ge=0)

    operating_system: str | None = Field(default=None, max_length=32)
    ip_address: str | None = None
    mac_address: str | None = None

    location_lat: float
    location_lon: float

    customer_rooting_jailbreak_indicator: bool = Field(default=0)
    customer_mobile_roaming_indicator: bool = Field(default=0)
    customer_vpn_indicator: bool = Field(default=0)
    customer_flag_terminal_malicious_behavior_1: bool = Field(default=0)
    customer_flag_terminal_malicious_behavior_2: bool = Field(default=0)
    customer_flag_terminal_malicious_behavior_3: bool = Field(default=0)
    customer_flag_terminal_malicious_behavior_5: bool = Field(default=0)
    customer_flag_terminal_malicious_behavior_6: bool = Field(default=0)

class TransactionResponseDTO(BaseModel):
    """저장된 거래와 ML·룰 탐지 결과를 반환하는 응답 DTO."""
    transaction_id: int = Field(strict=True, gt=0)

    # NOT_AVAILABLE은 ML 호출 실패가 아니라 아직 예측 결과가 없는 거래를 뜻한다.
    prediction_status: Literal["COMPLETED", "FAILED", "NOT_AVAILABLE"]

    predict_result: bool | None = None
    predict_proba: float | None = None

    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None

    created_at: datetime

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
    "MAC_ADDRESS_PATTERN",
    "TransactionLabelResponseDTO",
    "TransactionLabelUpdateDTO",
    "TransactionRequestDTO",
    "TransactionResponseDTO",
]
