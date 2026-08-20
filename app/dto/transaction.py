from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

    # 외부 요청과 ML에서는 customer_* 이름을 사용하고, 저장할 때 DB 필드로 옮긴다.
    customer_rooting_jailbreak_indicator: bool = Field(default=False)
    customer_mobile_roaming_indicator: bool = Field(default=False)
    customer_vpn_indicator: bool = Field(default=False)
    customer_flag_terminal_malicious_behavior_1: bool = Field(default=False)
    customer_flag_terminal_malicious_behavior_2: bool = Field(default=False)
    customer_flag_terminal_malicious_behavior_3: bool = Field(default=False)
    customer_flag_terminal_malicious_behavior_5: bool = Field(default=False)
    customer_flag_terminal_malicious_behavior_6: bool = Field(default=False)


class TransactionResponseDTO(BaseModel):
    """저장된 거래와 ML·룰 탐지 결과를 반환하는 응답 DTO."""

    transaction_id: int = Field(strict=True, gt=0)
    prediction_status: Literal["COMPLETED", "DECLINED"]
    predict_proba: float | None = None
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


class TransactionLabelStatus(str, Enum):
    """거래 라벨링 목록에서 선택할 담당자 판정 상태."""

    ALL = "ALL"
    UNLABELED = "UNLABELED"
    NORMAL = "NORMAL"
    FRAUD = "FRAUD"


class TransactionPredictionFilter(str, Enum):
    """거래 라벨링 목록에서 선택할 최신 ML 예측 상태."""

    ALL = "ALL"
    NORMAL = "NORMAL"
    FRAUD = "FRAUD"


class TransactionLabelQueueSummaryDTO(BaseModel):
    """전체 거래의 담당자 라벨 현황."""

    total_count: int = Field(ge=0)
    unlabeled_count: int = Field(ge=0)
    normal_count: int = Field(ge=0)
    fraud_count: int = Field(ge=0)


class TransactionLabelQueueItemDTO(BaseModel):
    """담당자가 한 화면에서 예측과 거래 정보를 비교할 수 있는 목록 행."""

    transaction_id: int = Field(strict=True, gt=0)
    customer_id: int | None = None
    transaction_datetime: datetime
    transaction_amount: int
    channel: str
    transaction_status: TransactionStatus | None = None
    source_account_number: str
    recipient_account_number: str
    initial_balance: int | None = None
    balance: int | None = None
    access_medium: str | None = None
    operating_system: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    num_connection_failure: int
    rooting_jailbreak_indicator: bool
    mobile_roaming_indicator: bool
    vpn_indicator: bool
    terminal_malicious_behavior_detected: bool
    predict_result: bool | None = None
    predict_proba: float | None = Field(default=None, ge=0, le=1)
    model_name: str | None = None
    model_version: str | None = None
    predicted_at: datetime | None = None
    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None


class TransactionLabelQueueResponseDTO(BaseModel):
    """라벨링 목록과 화면 상단 집계를 함께 반환한다."""

    items: list[TransactionLabelQueueItemDTO]
    summary: TransactionLabelQueueSummaryDTO
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_count: int = Field(ge=0)


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
    "TransactionLabelQueueItemDTO",
    "TransactionLabelQueueResponseDTO",
    "TransactionLabelQueueSummaryDTO",
    "TransactionLabelResponseDTO",
    "TransactionLabelStatus",
    "TransactionLabelUpdateDTO",
    "TransactionPredictionFilter",
    "TransactionRequestDTO",
    "TransactionResponseDTO",
]
