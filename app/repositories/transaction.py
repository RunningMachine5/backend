from datetime import UTC, datetime
from hashlib import sha256

from sqlmodel import Session, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_prediction import MLTransactionFeatures
from app.dto.transaction import TransactionCreateDTO
from app.services.features.ml_feature_assembler import (
    FeatureAssemblyError,
    assemble_ml_features,
    build_account_fields,
    build_customer_fields,
    build_derived_features_fields,
    build_transaction_fields,
)


def _account_id(account_number: str) -> str:
    """CSV 계좌번호를 현재 ERD의 내부 account_id로 안정적으로 변환한다."""

    if len(account_number) <= 64:
        return account_number
    digest = sha256(account_number.encode("utf-8")).hexdigest()
    return f"ACC_{digest[:32]}"


class CustomerIdentificationConflictError(RuntimeError):
    """같은 식별번호가 서로 다른 고객 ID에 사용된 경우."""


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: str) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def load_ml_features(
        self,
        transaction: Transaction,
    ) -> MLTransactionFeatures | None:
        """저장된 평탄 컬럼에서 ML 54개 Feature 계약을 다시 조립한다.

        파생 피처 행이 없거나 외부 계좌 정보만 있는 등 계약을 복원할 수 없는
        경우에는 호출 측이 부분 응답을 만들 수 있도록 None을 반환한다.
        """

        derived = self.session.get(DerivedFeatures, transaction.transaction_id)
        customer = self.session.get(Customer, transaction.customer_id)
        source_account = self.session.get(Account, transaction.source_account_id)
        if derived is None or customer is None or source_account is None:
            return None

        try:
            return assemble_ml_features(
                customer=customer,
                source_account=source_account,
                transaction=transaction,
                derived=derived,
            )
        except FeatureAssemblyError:
            return None

    def add_received(self, payload: TransactionCreateDTO) -> Transaction:
        features = payload.raw_features

        customer = self._upsert_customer(payload, features)
        source_account_id = self._upsert_source_account(payload, features)
        recipient_account_id = self._upsert_recipient_account(payload)

        transaction = Transaction(
            transaction_id=payload.transaction_id,
            customer_id=customer.customer_id,
            source_account_id=source_account_id,
            recipient_account_id=recipient_account_id,
            ip_address=payload.ip_address,
            mac_address=payload.mac_address,
            **build_transaction_fields(features),
        )
        self.session.add(transaction)
        self.session.add(
            DerivedFeatures(
                transaction_id=payload.transaction_id,
                **build_derived_features_fields(features),
            )
        )

        if payload.confirmed_is_fraud is not None:
            self.session.add(
                TransactionLabel(
                    transaction_id=payload.transaction_id,
                    confirmed_is_fraud=payload.confirmed_is_fraud,
                )
            )
        return transaction

    def _upsert_customer(
        self,
        payload: TransactionCreateDTO,
        features: MLTransactionFeatures,
    ) -> Customer:
        customer = self.session.get(Customer, payload.customer_id)
        if customer is None:
            customer_with_identification = self.session.exec(
                select(Customer).where(
                    Customer.identification_number
                    == payload.customer_identification_number
                )
            ).first()
            if customer_with_identification is not None:
                raise CustomerIdentificationConflictError(
                    payload.customer_identification_number
                )
            customer = Customer(
                customer_id=payload.customer_id,
                personal_identifier=payload.customer_personal_identifier,
                identification_number=payload.customer_identification_number,
                **build_customer_fields(features),
            )
            self.session.add(customer)
            return customer

        if customer.identification_number != payload.customer_identification_number:
            raise CustomerIdentificationConflictError(
                payload.customer_identification_number
            )
        return customer

    def _upsert_source_account(
        self,
        payload: TransactionCreateDTO,
        features: MLTransactionFeatures,
    ) -> str:
        """출금 계좌를 만들거나 가변 상태(잔액·잔여한도)를 최신으로 갱신한다."""

        source_account_id = _account_id(payload.source_account_number)
        account_fields = build_account_fields(features)
        source_account = self.session.get(Account, source_account_id)
        if source_account is None:
            source_account = Account(
                account_id=source_account_id,
                customer_id=payload.customer_id,
                account_number=payload.source_account_number,
                **account_fields,
            )
        else:
            for field_name, value in account_fields.items():
                setattr(source_account, field_name, value)
            source_account.updated_at = datetime.now()
        self.session.add(source_account)
        return source_account_id

    def _upsert_recipient_account(
        self,
        payload: TransactionCreateDTO,
    ) -> str | None:
        """외부 수취 계좌는 식별 정보만 알 수 있으므로 나머지는 NULL로 둔다."""

        if not payload.recipient_account_number:
            return None

        recipient_account_id = _account_id(payload.recipient_account_number)
        if self.session.get(Account, recipient_account_id) is None:
            self.session.add(
                Account(
                    account_id=recipient_account_id,
                    customer_id=None,
                    account_number=payload.recipient_account_number,
                )
            )
        return recipient_account_id


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
