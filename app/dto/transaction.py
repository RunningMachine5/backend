import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)

MAC_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

class TransactionRequestDTO(BaseModel):
    """
    외부 클라이언트가 보내는 거래 원시 데이터.
    계좌 정보와
    """
    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = Field(default=None, min_length=1, max_length=64) # atm, 지점 거래의 경우 None.
    account_account_number: str = Field(min_length=8, max_length=32)
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


class TransactionResponseDTO(BaseModel):
    """저장된 거래와 ML·룰 탐지 결과를 반환하는 응답 DTO."""
    transaction_id: int

    prediction_status: Literal["COMPLETED", "FAILED"]

    predict_result: bool | None = None
    predict_proba: float | None = None

    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None

    created_at: datetime

class TransactionLabelUpdateDTO(BaseModel):
    """담당자가 확정한 거래의 이진 정답 라벨."""

    model_config = ConfigDict(extra="forbid")

    confirmed_is_fraud: StrictBool


class TransactionLabelResponseDTO(BaseModel):
    """저장된 거래 정답 라벨 응답."""

    transaction_id: str
    confirmed_is_fraud: bool
    labeled_at: datetime

__all__ = [
    "MAC_ADDRESS_PATTERN",
    "TransactionRequestDTO",
    "TransactionResponseDTO",
    "TransactionLabelResponseDTO",
    "TransactionLabelUpdateDTO",
]
