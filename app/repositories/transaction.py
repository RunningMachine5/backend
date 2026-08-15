from datetime import UTC, datetime, timedelta
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
from app.dto.ml_features import MLTransactionFeatures
from app.dto.transaction import TransactionRequestDTO
from app.services.features.ml_feature_assembler import assemble_ml_features


def _account_id(account_number: str) -> str:
    """CSV 계좌번호를 accounts.id에 저장할 내부 식별자로 안정적으로 변환한다."""

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


class CustomerReferenceNotFoundError(RuntimeError):
    """거래가 참조한 고객이 고객 원장에 아직 등록되지 않은 경우."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"고객 원장에서 customer_id를 찾을 수 없습니다: {customer_id}")


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: int) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def load_ml_features(
        self,
        transaction: Transaction,
    ) -> MLTransactionFeatures:
        """저장된 정규화 컬럼에서 ML raw59 Feature 계약을 다시 조립한다."""

        derived = self.session.get(DerivedFeatures, transaction.id)
        customer = (
            self.session.get(Customer, transaction.customer_id)
            if transaction.customer_id is not None
            else None
        )
        source_account = self.session.exec(
            select(Account).where(
                Account.account_number == transaction.source_account_number
            )
        ).first()
        recipient_account = (
            self.session.exec(
                select(Account).where(
                    Account.account_number == transaction.recipient_account_number
                )
            ).first()
            if transaction.recipient_account_number
            else None
        )
        return assemble_ml_features(
            customer=customer,
            source_account=source_account,
            recipient_account=recipient_account,
            transaction=transaction,
            derived=derived,
        )

    def add_received(self, payload: TransactionRequestDTO) -> Transaction:
        customer = self._find_customer(payload.customer_id)
        source_account_number = self._upsert_source_account(payload, customer)
        recipient_account_number = self._upsert_recipient_account(payload)
        # Transaction은 출금·수취 계좌 FK를 모두 참조한다. ORM relationship이
        # 없는 mapper들의 순서에 기대지 않고 계좌 INSERT/UPDATE를 먼저 확정한다.
        self.session.flush()

        transaction = Transaction(
            customer_id=(customer.id if customer is not None else None),
            source_account_number=source_account_number,
            recipient_account_number=recipient_account_number,
            transaction_datetime=payload.transaction_datetime,
            transaction_amount=payload.transaction_amount,
            channel=payload.channel.lower(),
            type_general_automatic=payload.type_general_automatic.lower(),
            access_medium=(
                payload.access_medium.lower() if payload.access_medium else None
            ),
            error_code=None,
            num_connection_failure=payload.num_connection_failure,
            another_person_account=False,
            initial_balance=None,
            balance=None,
            remaining_amount_daily_limit_exceeded=None,
            operating_system=payload.operating_system,
            ip_address=payload.ip_address,
            mac_address=payload.mac_address,
            location=f"{payload.location_lat} {payload.location_lon}",
            location_lat=payload.location_lat,
            location_lon=payload.location_lon,
            rooting_jailbreak_indicator=(payload.customer_rooting_jailbreak_indicator),
            mobile_roaming_indicator=payload.customer_mobile_roaming_indicator,
            vpn_indicator=payload.customer_vpn_indicator,
            flag_terminal_malicious_behavior_1=(
                payload.customer_flag_terminal_malicious_behavior_1
            ),
            flag_terminal_malicious_behavior_2=(
                payload.customer_flag_terminal_malicious_behavior_2
            ),
            flag_terminal_malicious_behavior_3=(
                payload.customer_flag_terminal_malicious_behavior_3
            ),
            flag_terminal_malicious_behavior_5=(
                payload.customer_flag_terminal_malicious_behavior_5
            ),
            flag_terminal_malicious_behavior_6=(
                payload.customer_flag_terminal_malicious_behavior_6
            ),
        )
        self.session.add(transaction)
        self.session.flush()
        assert transaction.id is not None

        # 아직 실시간 파생 계산기가 없으므로, ML 입력 59개의 자리를
        # 비워 두지 않고 중립적인 기본값으로 저장한다. 나중에 계산 로직이
        # 준비되면 같은 transaction.id의 행을 실제 값으로 갱신하면 된다.
        self.session.add(self._default_derived_features(transaction.id))
        return transaction

    @staticmethod
    def _default_derived_features(transaction_id: int) -> DerivedFeatures:
        """실제 파생 계산기가 없는 동안 사용할 임시 스냅샷을 만든다.

        수치형은 0, 상태형은 False, 과거 시각은 None을 사용한다.
        이 값은 '이상 징후 없음'을 가정한 테스트용 기본값이지,
        실제 거래 이력을 계산한 결과가 아니다.
        """

        return DerivedFeatures(
            id=transaction_id,
            distance=0.0,
            time_difference=timedelta(0),
            one_month_max_amount=0,
            one_month_std_dev=0.0,
            dawn_one_month_max_amount=0,
            dawn_one_month_std_dev=0.0,
            unused_terminal_status=False,
            unused_account_status=False,
            transaction_history_with_the_account=0,
            flag_deposit_more_than_tenMillion=False,
            number_of_transaction_with_the_account=0,
            last_atm_transaction_datetime=None,
            last_bank_branch_transaction_datetime=None,
            flag_change_of_authentication_1=False,
            flag_change_of_authentication_2=False,
            flag_change_of_authentication_3=False,
            flag_change_of_authentication_4=False,
            inquiry_atm_limit=False,
            increase_atm_limit=False,
            release_suspension=False,
            transaction_resumed_date=None,
            recipient_account_suspend_status=False,
            first_time_ios_by_vulnerable_user=False,
        )

    def _find_customer(self, customer_id: str | None) -> Customer | None:
        if customer_id is None:
            return None
        customer = self.session.get(Customer, customer_id)
        if customer is None:
            raise CustomerReferenceNotFoundError(customer_id)
        return customer

    def _upsert_source_account(
        self,
        payload: TransactionRequestDTO,
        customer: Customer | None,
    ) -> str:
        """출금 계좌 식별자를 보존하고 알려진 고객 소유권만 검증한다."""

        source_account_id = _account_id(payload.source_account_number)
        source_account = self.session.exec(
            select(Account).where(
                Account.account_number == payload.source_account_number
            )
        ).first()
        if source_account is None:
            conflicting_account = self.session.get(Account, source_account_id)
            if conflicting_account is not None:
                raise AccountIdentifierConflictError(
                    source_account_id,
                    ["account_number"],
                )
            source_account = Account(
                id=source_account_id,
                customer_id=(customer.id if customer is not None else None),
                account_number=payload.source_account_number,
            )
        else:
            if source_account.customer_id is None and customer is not None:
                source_account.customer_id = customer.id
            elif (
                customer is not None
                and source_account.customer_id is not None
                and source_account.customer_id != customer.id
            ):
                raise AccountOwnershipConflictError(
                    source_account_id,
                    stored_customer_id=source_account.customer_id,
                    requested_customer_id=customer.id,
                )
            source_account.updated_at = datetime.now(UTC)
        self.session.add(source_account)
        return source_account.account_number

    def _upsert_recipient_account(
        self,
        payload: TransactionRequestDTO,
    ) -> str | None:
        """외부 수취 계좌는 식별 정보만 알 수 있으므로 나머지는 NULL로 둔다."""

        if not payload.recipient_account_number:
            return None

        recipient_account_id = _account_id(payload.recipient_account_number)
        recipient_account = self.session.exec(
            select(Account).where(
                Account.account_number == payload.recipient_account_number
            )
        ).first()
        if recipient_account is None:
            conflicting_account = self.session.get(Account, recipient_account_id)
            if conflicting_account is not None:
                raise AccountIdentifierConflictError(
                    recipient_account_id,
                    ["account_number"],
                )
            self.session.add(
                Account(
                    id=recipient_account_id,
                    customer_id=None,
                    account_number=payload.recipient_account_number,
                )
            )
        return payload.recipient_account_number


class TransactionLabelRepository:
    """담당자가 확정한 이진 라벨을 거래별 한 행으로 관리한다."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: int) -> TransactionLabel | None:
        return self.session.get(TransactionLabel, transaction_id)

    def upsert(
        self,
        *,
        transaction_id: int,
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
        transaction_id: int,
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
                Account,
                Account | None,
                DerivedFeatures,
            ]
        ],
        bool,
    ]:
        """최신 ML 결과가 양성인 최근 거래와 raw59 조립 행을 한 번에 읽는다.

        양성 예측부터 거르면 과거 양성·최신 음성인 거래가 섞이므로 거래별 최신
        예측을 먼저 확정한다. 거래시각과 거래 ID를 함께 정렬해 같은 데이터에서는
        항상 같은 표본을 고르고, ``limit + 1``건으로 다음 표본 존재 여부를 구한다.
        출금계좌와 파생 피처가 조립된 거래만 고르고, 선택한 행은 별도 누락
        검증 없이 바로 raw59로 재조립한다.
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
                MLPredictionResult.transaction_id == Transaction.id,
            )
            .join(
                ranked_predictions,
                ranked_predictions.c.prediction_result_id == MLPredictionResult.id,
            )
            .outerjoin(Customer, Customer.id == Transaction.customer_id)
            .join(
                source_account,
                source_account.account_number == Transaction.source_account_number,
            )
            .outerjoin(
                recipient_account,
                recipient_account.account_number
                == Transaction.recipient_account_number,
            )
            .join(
                DerivedFeatures,
                DerivedFeatures.id == Transaction.id,
            )
            .where(
                ranked_predictions.c.prediction_rank == 1,
                MLPredictionResult.predict_result.is_(True),
            )
            .order_by(
                Transaction.transaction_datetime.desc(),
                Transaction.id.desc(),
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
    "CustomerReferenceNotFoundError",
    "PredictionResultRepository",
    "TransactionLabelRepository",
    "TransactionRepository",
]
