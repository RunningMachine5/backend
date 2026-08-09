from datetime import UTC, date, datetime
from hashlib import sha256

from sqlmodel import Session, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.transaction import TransactionCreateDTO


def _account_id(account_number: str) -> str:
    """CSV 계좌번호를 현재 ERD의 내부 account_id로 안정적으로 변환한다."""

    if len(account_number) <= 64:
        return account_number
    digest = sha256(account_number.encode("utf-8")).hexdigest()
    return f"ACC_{digest[:32]}"


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: str) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def add_received(self, payload: TransactionCreateDTO) -> Transaction:
        features = payload.raw_features
        raw_features = features.model_dump(mode="json", by_alias=True)

        customer = self.session.get(Customer, payload.customer_id)
        if customer is None:
            customer = Customer(
                customer_id=payload.customer_id,
                birth_date=(
                    payload.customer_birth_date
                    or date(features.Customer_Birthyear, 1, 1)
                ),
                gender=features.Customer_Gender,
                personal_identifier=payload.customer_personal_identifier,
                identification_number=payload.customer_identification_number,
                registration_datetime=features.Customer_registration_datetime,
                credit_rating=str(features.Customer_credit_rating),
            )
            self.session.add(customer)
        elif payload.customer_birth_date is not None:
            # 이전 거래에서 출생연도만 받아 1월 1일로 보완했더라도,
            # 실제 생년월일이 들어오면 최신 원본 값으로 교체한다.
            customer.birth_date = payload.customer_birth_date
            self.session.add(customer)

        source_account_id = _account_id(payload.source_account_number)
        source_account = self.session.get(Account, source_account_id)
        if source_account is None:
            source_account = Account(
                account_id=source_account_id,
                customer_id=payload.customer_id,
                account_number=payload.source_account_number,
                account_type=features.Account_account_type,
                creation_datetime=features.Account_creation_datetime,
            )
            self.session.add(source_account)

        recipient_account_id: str | None = None
        if payload.recipient_account_number:
            recipient_account_id = _account_id(payload.recipient_account_number)
            recipient_account = self.session.get(Account, recipient_account_id)
            if recipient_account is None:
                recipient_account = Account(
                    account_id=recipient_account_id,
                    customer_id=None,
                    account_number=payload.recipient_account_number,
                    account_type=None,
                    creation_datetime=None,
                )
                self.session.add(recipient_account)

        transaction = Transaction(
            transaction_id=payload.transaction_id,
            customer_id=payload.customer_id,
            source_account_id=source_account_id,
            recipient_account_id=recipient_account_id,
            transaction_datetime=features.Transaction_Datetime,
            transaction_amount=features.Transaction_Amount,
            channel=features.Channel,
            location=features.Location,
            raw_features=raw_features,
        )
        self.session.add(transaction)

        if payload.confirmed_is_fraud is not None:
            self.session.add(
                TransactionLabel(
                    transaction_id=payload.transaction_id,
                    confirmed_is_fraud=payload.confirmed_is_fraud,
                )
            )
        return transaction


class TransactionLabelRepository:
    """담당자가 확정한 이진 라벨을 거래별 한 행으로 관리한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: str) -> TransactionLabel | None:
        return self.session.get(TransactionLabel, transaction_id)

    def upsert(
        self,
        *,
        transaction_id: str,
        confirmed_is_fraud: bool,
    ) -> TransactionLabel:
        label = self.get(transaction_id)
        if label is None:
            label = TransactionLabel(
                transaction_id=transaction_id,
                confirmed_is_fraud=confirmed_is_fraud,
            )
            self.session.add(label)
        elif label.confirmed_is_fraud != confirmed_is_fraud:
            label.confirmed_is_fraud = confirmed_is_fraud
            label.labeled_at = datetime.now(UTC)
            self.session.add(label)
        return label


class PredictionResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, result: MLPredictionResult) -> None:
        self.session.add(result)

    def latest_for_transaction(
        self,
        transaction_id: str,
    ) -> MLPredictionResult | None:
        return self.session.exec(
            select(MLPredictionResult)
            .where(MLPredictionResult.transaction_id == transaction_id)
            .order_by(
                MLPredictionResult.created_at.desc(),
                MLPredictionResult.id.desc(),
            )
            .limit(1)
        ).first()


__all__ = [
    "PredictionResultRepository",
    "TransactionLabelRepository",
    "TransactionRepository",
]
