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


@dataclass(frozen=True)
class TransactionFeaturesDTO:
    """6개 중 연관성이 높은 피쳐들만 넘길 수 있도록 정의한 입력 DTO."""

    user_id: str
    is_fraud: bool
    high_relevance_feature: dict
    fraud_probability: float