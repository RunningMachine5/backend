from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionDTO:
    """실제 64개 거래 컬럼 중 데모에 필요한 6개 컬럼만 정의한 입력 DTO."""

    user_id: str
    user_name: str
    email: str
    transaction_time: str
    amount: int
    user_amount_std_dev: float
    payment_method: str
    merchant_category: str

