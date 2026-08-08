"""Backend가 ML Serving에 전달하는 전처리 전 거래 Feature 계약."""

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


BinaryFlag = Literal[0, 1]


class MLTransactionFeatures(BaseModel):
    """새 전처리가 요구하는 원본 거래 Feature 54개.

    Backend는 이 값들을 One-hot Encoding하지 않고 원본 형태로 저장한 뒤
    ML Serving에 그대로 전달한다. 날짜 파생변수, 위치 분리, One-hot Encoding과
    학습 Schema 정렬은 ML Serving의 책임이다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    Customer_Birthyear: int = Field(ge=1947, le=2005)
    Customer_Gender: Literal["male", "female"]
    Customer_registration_datetime: datetime
    Customer_credit_rating: int = Field(ge=1, le=9)
    Customer_flag_change_of_authentication_1: BinaryFlag
    Customer_flag_change_of_authentication_2: BinaryFlag
    Customer_flag_change_of_authentication_3: BinaryFlag
    Customer_flag_change_of_authentication_4: BinaryFlag
    Customer_rooting_jailbreak_indicator: BinaryFlag
    Customer_mobile_roaming_indicator: BinaryFlag
    Customer_VPN_Indicator: BinaryFlag
    Customer_loan_type: Literal["a", "b", "c", "d", "e"]
    Customer_flag_terminal_malicious_behavior_1: BinaryFlag
    Customer_flag_terminal_malicious_behavior_2: BinaryFlag
    Customer_flag_terminal_malicious_behavior_3: BinaryFlag
    Customer_flag_terminal_malicious_behavior_5: BinaryFlag
    Customer_flag_terminal_malicious_behavior_6: BinaryFlag
    Customer_inquery_atm_limit: BinaryFlag
    Customer_increase_atm_limit: BinaryFlag

    Account_account_type: Literal["a", "b", "c", "d"]
    Account_creation_datetime: datetime
    Account_initial_balance: int = Field(ge=0)
    Account_balance: int = Field(ge=0)
    Account_indicator_release_limit_excess: BinaryFlag
    Account_amount_daily_limit: int = Field(gt=0)
    Account_indicator_Openbanking: BinaryFlag
    Account_remaining_amount_daily_limit_exceeded: int = Field(ge=0)
    Account_release_suspension: BinaryFlag = Field(
        validation_alias=AliasChoices(
            "Account_release_suspension",
            "Account_release_suspention",
        ),
        # ML 54개 입력 계약과 기존 학습 데이터는 아직 오타 이름을 사용한다.
        # 입력은 두 이름을 받되 ML 전송·raw_data 저장 시에는 legacy 키를 유지한다.
        serialization_alias="Account_release_suspention",
    )
    Account_one_month_max_amount: int = Field(ge=0)
    Account_one_month_std_dev: float = Field(ge=0)
    Account_dawn_one_month_max_amount: int = Field(ge=0)
    Account_dawn_one_month_std_dev: float = Field(ge=0)

    Transaction_Datetime: datetime
    Transaction_Amount: int = Field(gt=0)
    Channel: Literal["mobile", "internet", "ATM", "Others"]
    Operating_System: Literal[
        "Android",
        "iOS",
        "Windows",
        "macOS",
        "Linux",
        "Others",
    ]
    Error_Code: Literal["a", "b", "c", "d", "e", "f"]
    Type_General_Automatic: Literal["general", "automatic"]
    Access_Medium: Literal["a", "b", "c", "d", "e", "f", "g", "h"]
    Location: str = Field(min_length=1)
    Transaction_num_connection_failure: int = Field(ge=0)
    Another_Person_Account: BinaryFlag
    Distance: float = Field(ge=0)
    time_difference: str = Field(alias="Time Difference", min_length=1)
    Unused_terminal_status: BinaryFlag
    Last_atm_transaction_datetime: datetime | None
    Last_bank_branch_transaction_datetime: datetime | None
    Flag_deposit_more_than_tenMillion: BinaryFlag
    Unused_account_status: BinaryFlag
    Recipient_account_suspend_status: BinaryFlag
    Number_of_transaction_with_the_account: int = Field(ge=0)
    Transaction_history_with_the_account: int = Field(ge=0)
    First_time_iOS_by_vulnerable_user: BinaryFlag
    Transaction_resumed_date: datetime | None


RAW_TRANSACTION_FEATURE_COLUMNS = tuple(
    field.serialization_alias or field.alias or name
    for name, field in MLTransactionFeatures.model_fields.items()
)


__all__ = [
    "MLTransactionFeatures",
    "RAW_TRANSACTION_FEATURE_COLUMNS",
]
