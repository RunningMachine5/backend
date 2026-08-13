from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func
from sqlalchemy.orm import aliased
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
    """거래 원장 참조값 충돌의 API 호환 기준 예외.

    기존 Pipeline이 이 예외를 409로 변환하므로 고객·계좌 원장의 다른 충돌도
    하위 예외로 표현한다. 호출자는 하위 타입과 ``conflicting_fields``로 실제
    원인을 구분할 수 있다.
    """


class AccountIdentifierConflictError(CustomerIdentificationConflictError):
    """하나의 내부 계좌 ID가 서로 다른 원본 계좌번호를 가리키는 경우."""

    def __init__(self, account_id: str, conflicting_fields: list[str]) -> None:
        self.account_id = account_id
        self.conflicting_fields = tuple(conflicting_fields)
        super().__init__(
            f"계좌 식별값이 기존 원장과 다릅니다: {account_id} "
            f"({', '.join(conflicting_fields)})"
        )


class AccountOwnershipConflictError(CustomerIdentificationConflictError):
    """이미 다른 고객이 소유한 계좌를 출금 계좌로 사용한 경우."""

    def __init__(
        self,
        account_id: str,
        *,
        stored_customer_id: str,
        requested_customer_id: str,
    ) -> None:
        self.account_id = account_id
        self.stored_customer_id = stored_customer_id
        self.requested_customer_id = requested_customer_id
        super().__init__(
            "계좌 소유 고객이 기존 원장과 다릅니다: "
            f"{account_id} ({stored_customer_id} != {requested_customer_id})"
        )


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: str) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def load_ml_features(
        self,
        transaction: Transaction,
    ) -> MLTransactionFeatures | None:
        """저장된 정규화 컬럼에서 ML raw59 Feature 계약을 다시 조립한다.

        파생 피처 행이 없거나 외부 계좌 정보만 있는 등 계약을 복원할 수 없는
        경우에는 호출 측이 부분 응답을 만들 수 있도록 None을 반환한다.
        """

        derived = self.session.get(DerivedFeatures, transaction.transaction_id)
        customer = self.session.get(Customer, transaction.customer_id)
        source_account = self.session.get(Account, transaction.source_account_id)
        recipient_account = (
            self.session.get(Account, transaction.recipient_account_id)
            if transaction.recipient_account_id
            else None
        )
        if (
            derived is None
            or customer is None
            or source_account is None
            or recipient_account is None
        ):
            return None

        try:
            return assemble_ml_features(
                customer=customer,
                source_account=source_account,
                recipient_account=recipient_account,
                transaction=transaction,
                derived=derived,
            )
        except FeatureAssemblyError:
            return None

    def add_received(self, payload: TransactionCreateDTO) -> Transaction:
        features = payload.raw_features

        customer = self._upsert_customer(payload, features)
        # 새 고객을 기존 수취 계좌의 소유자로 연결하는 UPDATE가 먼저 flush되면
        # accounts.customer_id FK가 실패한다. 계좌 조회가 일으키는 autoflush보다
        # 고객 INSERT를 앞세운다.
        self.session.flush()
        source_account_id = self._upsert_source_account(payload, features)
        recipient_account_id = self._upsert_recipient_account(payload)
        # Transaction은 출금·수취 계좌 FK를 모두 참조한다. ORM relationship이
        # 없는 mapper들의 순서에 기대지 않고 계좌 INSERT/UPDATE를 먼저 확정한다.
        self.session.flush()

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

        # DerivedFeatures는 Transaction FK를 가지지만 두 모델 사이에 ORM
        # relationship이 없어 Unit of Work가 mapper INSERT 순서를 보장하지
        # 않는다. 실제 PostgreSQL에서는 derived_features가 먼저 INSERT되어
        # 즉시 FK 위반이 날 수 있으므로 부모 거래를 같은 트랜잭션 안에서 먼저
        # flush한다. 이후 오류가 발생해도 Pipeline rollback이 전체를 되돌린다.
        self.session.flush()
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

        latest_customer_fields = {
            "personal_identifier": payload.customer_personal_identifier,
            **build_customer_fields(features),
        }
        for field_name, value in latest_customer_fields.items():
            setattr(customer, field_name, value)
        customer.updated_at = datetime.now(UTC)
        self.session.add(customer)
        return customer

    def _upsert_source_account(
        self,
        payload: TransactionCreateDTO,
        features: MLTransactionFeatures,
    ) -> str:
        """출금 계좌를 만들거나 마지막으로 처리된 요청값으로 갱신한다.

        생성 데이터는 같은 계좌의 공통 Feature가 항상 일관되지는 않으므로
        계좌번호와 소유 고객만 충돌을 막고 나머지 값은 최신 입력을 반영한다.
        """

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
            if source_account.account_number != payload.source_account_number:
                raise AccountIdentifierConflictError(
                    source_account_id,
                    ["account_number"],
                )
            if source_account.customer_id is None:
                source_account.customer_id = payload.customer_id
            elif source_account.customer_id != payload.customer_id:
                raise AccountOwnershipConflictError(
                    source_account_id,
                    stored_customer_id=source_account.customer_id,
                    requested_customer_id=payload.customer_id,
                )

            for field_name, value in account_fields.items():
                setattr(source_account, field_name, value)
            source_account.updated_at = datetime.now(UTC)
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
        suspend_status = payload.raw_features.recipient_account_suspend_status
        recipient_account = self.session.get(Account, recipient_account_id)
        if recipient_account is None:
            self.session.add(
                Account(
                    account_id=recipient_account_id,
                    customer_id=None,
                    account_number=payload.recipient_account_number,
                    suspend_status=suspend_status,
                )
            )
        else:
            if recipient_account.account_number != payload.recipient_account_number:
                raise AccountIdentifierConflictError(
                    recipient_account_id,
                    ["account_number"],
                )
            recipient_account.suspend_status = suspend_status
            recipient_account.updated_at = datetime.now(UTC)
            self.session.add(recipient_account)
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

    def latest_positive_feature_rows(
        self,
        *,
        limit: int,
    ) -> tuple[
        list[
            tuple[
                Transaction,
                Customer | None,
                Account | None,
                Account | None,
                DerivedFeatures | None,
            ]
        ],
        bool,
    ]:
        """최신 ML 결과가 양성인 최근 거래와 raw59 조립 행을 한 번에 읽는다.

        양성 예측부터 거르면 과거 양성·최신 음성인 거래가 섞이므로 거래별 최신
        예측을 먼저 확정한다. 거래시각과 거래 ID를 함께 정렬해 같은 데이터에서는
        항상 같은 표본을 고르고, ``limit + 1``건으로 다음 표본 존재 여부를 구한다.
        고객·출금계좌·파생 피처는 outer join하여 손상된 거래도 조용히 누락하지 않고
        리플레이 오류 상세로 보고할 수 있게 한다.
        """

        ranked_predictions = select(
            MLPredictionResult.id.label("prediction_result_id"),
            func.row_number()
            .over(
                partition_by=MLPredictionResult.transaction_id,
                order_by=(
                    MLPredictionResult.created_at.desc(),
                    MLPredictionResult.id.desc(),
                ),
            )
            .label("prediction_rank"),
        ).subquery()

        source_account = aliased(Account)
        recipient_account = aliased(Account)
        statement = (
            select(
                Transaction,
                Customer,
                source_account,
                recipient_account,
                DerivedFeatures,
            )
            .select_from(Transaction)
            .join(
                MLPredictionResult,
                MLPredictionResult.transaction_id == Transaction.transaction_id,
            )
            .join(
                ranked_predictions,
                ranked_predictions.c.prediction_result_id == MLPredictionResult.id,
            )
            .outerjoin(Customer, Customer.customer_id == Transaction.customer_id)
            .outerjoin(
                source_account,
                source_account.account_id == Transaction.source_account_id,
            )
            .outerjoin(
                recipient_account,
                recipient_account.account_id == Transaction.recipient_account_id,
            )
            .outerjoin(
                DerivedFeatures,
                DerivedFeatures.transaction_id == Transaction.transaction_id,
            )
            .where(
                ranked_predictions.c.prediction_rank == 1,
                MLPredictionResult.prediction_is_fraud.is_(True),
            )
            .order_by(
                Transaction.transaction_datetime.desc(),
                Transaction.transaction_id.desc(),
            )
            .limit(limit + 1)
        )
        rows = [
            (
                transaction,
                customer,
                source,
                recipient,
                derived,
            )
            for transaction, customer, source, recipient, derived in self.session.exec(
                statement
            ).all()
        ]
        return rows[:limit], len(rows) > limit


__all__ = [
    "AccountIdentifierConflictError",
    "AccountOwnershipConflictError",
    "CustomerIdentificationConflictError",
    "PredictionResultRepository",
    "TransactionLabelRepository",
    "TransactionRepository",
]
