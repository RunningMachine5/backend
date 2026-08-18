from datetime import UTC, datetime

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
from app.repositories.feature_context import TEMP_ACCOUNT_ID_PREFIX
from app.services.features.ml_feature_assembler import assemble_ml_features


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: int) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def load_ml_features(
        self,
        transaction: Transaction,
    ) -> MLTransactionFeatures:
        """저장된 정규화 컬럼에서 ML raw51 Feature 계약을 다시 조립한다."""

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

    def add_received(
        self,
        payload: TransactionRequestDTO,
        features: MLTransactionFeatures,
    ) -> Transaction:
        source_account = self.session.exec(
            select(Account).where(
                Account.account_number == payload.source_account_number
            )
        ).one()
        recipient_account = self.session.exec(
            select(Account).where(
                Account.account_number == payload.recipient_account_number
            )
        ).one()

        transaction = Transaction(
            customer_id=source_account.customer_id,
            source_account_number=source_account.account_number,
            recipient_account_number=recipient_account.account_number,
            transaction_datetime=payload.transaction_datetime,
            transaction_amount=payload.transaction_amount,
            channel=payload.channel.lower(),
            type_general_automatic=payload.type_general_automatic.lower(),
            access_medium=(
                payload.access_medium.lower() if payload.access_medium else None
            ),
            error_code=None,
            num_connection_failure=payload.num_connection_failure,
            another_person_account=features.another_person_account,
            initial_balance=features.account_initial_balance,
            balance=features.account_balance,
            remaining_amount_daily_limit_exceeded=(
                features.account_remaining_amount_daily_limit_exceeded
            ),
            operating_system=payload.operating_system,
            ip_address=payload.ip_address,
            mac_address=payload.mac_address,
            location_lat=payload.location_lat,
            location_lon=payload.location_lon,
            rooting_jailbreak_indicator=payload.rooting_jailbreak_indicator,
            mobile_roaming_indicator=payload.mobile_roaming_indicator,
            vpn_indicator=payload.vpn_indicator,
            flag_terminal_malicious_behavior_1=(
                payload.flag_terminal_malicious_behavior_1
            ),
            flag_terminal_malicious_behavior_2=(
                payload.flag_terminal_malicious_behavior_2
            ),
            flag_terminal_malicious_behavior_3=(
                payload.flag_terminal_malicious_behavior_3
            ),
            flag_terminal_malicious_behavior_5=(
                payload.flag_terminal_malicious_behavior_5
            ),
            flag_terminal_malicious_behavior_6=(
                payload.flag_terminal_malicious_behavior_6
            ),
        )
        self.session.add(transaction)
        self.session.flush()
        assert transaction.id is not None

        # 담당자 Feature 서비스가 ML에 보낸 값과 같은 스냅샷을 저장한다.
        # 룰과 재학습 데이터도 이 행을 읽으므로 다시 계산하지 않는다.
        self.session.add(self._derived_features_from_ml(transaction.id, features))
        return transaction

    @staticmethod
    def _derived_features_from_ml(
        transaction_id: int,
        features: MLTransactionFeatures,
    ) -> DerivedFeatures:
        """담당자 Feature 서비스의 계산 결과를 저장 모델로 옮긴다."""

        return DerivedFeatures(
            id=transaction_id,
            distance=features.distance,
            time_difference=features.time_difference,
            one_month_max_amount=features.account_one_month_max_amount,
            one_month_std_dev=features.account_one_month_std_dev,
            dawn_one_month_max_amount=features.account_dawn_one_month_max_amount,
            dawn_one_month_std_dev=features.account_dawn_one_month_std_dev,
            unused_terminal_status=features.unused_terminal_status,
            unused_account_status=features.unused_account_status,
            transaction_history_with_the_account=(
                features.transaction_history_with_the_account
            ),
            flag_deposit_more_than_ten_million=(
                features.flag_deposit_more_than_ten_million
            ),
            number_of_transaction_with_the_account=(
                features.number_of_transaction_with_the_account
            ),
            last_atm_transaction_datetime=features.last_atm_transaction_datetime,
            last_bank_branch_transaction_datetime=(
                features.last_bank_branch_transaction_datetime
            ),
            flag_change_of_authentication_1=(
                features.customer_flag_change_of_authentication_1
            ),
            flag_change_of_authentication_2=(
                features.customer_flag_change_of_authentication_2
            ),
            flag_change_of_authentication_3=(
                features.customer_flag_change_of_authentication_3
            ),
            flag_change_of_authentication_4=(
                features.customer_flag_change_of_authentication_4
            ),
            inquiry_atm_limit=features.customer_inquery_atm_limit,
            increase_atm_limit=features.customer_increase_atm_limit,
            release_suspension=features.recipient_release_suspension,
            recipient_transaction_resumed_date=(
                features.recipient_transaction_resumed_date
            ),
            recipient_account_suspend_status=(
                features.recipient_account_suspend_status
            ),
        )

    def apply_approved_balance(
        self,
        transaction: Transaction,
        features: MLTransactionFeatures,
    ) -> None:
        """정상 거래로 판정된 경우에만 출금 계좌 잔액을 반영한다."""

        source_account = self.session.exec(
            select(Account).where(
                Account.account_number == transaction.source_account_number
            )
        ).one()
        # 임시 계좌는 실제 잔액 원장이 아니므로 추론용 기본값을 그대로 둔다.
        if source_account.id.startswith(TEMP_ACCOUNT_ID_PREFIX):
            return
        source_account.current_balance = features.account_balance
        source_account.remaining_daily_limit = (
            features.account_remaining_amount_daily_limit_exceeded
        )
        source_account.updated_at = datetime.now(UTC)
        self.session.add(source_account)

    # doo
    def save_transaction(self, transaction: Transaction) -> Transaction:
        self.session.add(transaction)
        self.session.flush()
        self.session.refresh(transaction)
        return transaction


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
        """최신 ML 결과가 양성인 최근 거래와 raw51 조립 행을 한 번에 읽는다.

        양성 예측부터 거르면 과거 양성·최신 음성인 거래가 섞이므로 거래별 최신
        예측을 먼저 확정한다. 거래시각과 거래 ID를 함께 정렬해 같은 데이터에서는
        항상 같은 표본을 고르고, ``limit + 1``건으로 다음 표본 존재 여부를 구한다.
        출금계좌와 파생 피처가 조립된 거래만 고르고, 선택한 행은 별도 누락
        검증 없이 바로 raw51로 재조립한다.
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
    "PredictionResultRepository",
    "TransactionLabelRepository",
    "TransactionRepository",
]
