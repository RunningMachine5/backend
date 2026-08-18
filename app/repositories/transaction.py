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


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: int) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

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
