from datetime import date, datetime, time, timedelta

from pydantic.dataclasses import dataclass
from sqlalchemy import func, select
from sqlmodel import Session, col

from app.data.model import Account, Customer, CustomerEvent, Transaction

@dataclass(frozen=True)
class FeatureContext:
    source_account: Account
    customer: Customer
    recipient_account: Account

@dataclass(frozen=True)
class AggregationFeatures:
    count_3h: int
    count_10m_in_week: int
    one_month_max: int
    one_month_std_dev: float
    dawn_one_month_max: int
    dawn_one_month_std_dev: float
    transaction_count_with: int
    tx_mac_count: int
    last_atm_datetime: datetime | None
    last_branch_datetime: datetime | None
    today_amount: int

class FeatureContextRepository:
    """
    거래 데이터를 채우거나 파생 피쳐의 값을 구할 데이터를 조회한다.
    """
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_feature_context(
        self,
        account_number: str,
        recipient_account_number: str,
    ) -> FeatureContext:
        """미리 저장된 고객·출금계좌·수취계좌를 한 번씩 조회한다."""
        statement = (
            select(Customer, Account)
            .join(Account, Customer.id == Account.customer_id)
            .where(Account.account_number == account_number)
        )
        customer, source_account = self.session.exec(statement).one()

        if recipient_account_number == account_number:
            recipient_account = source_account
        else:
            statement = (
                select(Account)
                .where(Account.account_number == recipient_account_number)
            )
            recipient_account = self.session.exec(statement).scalars().one()

        return FeatureContext(
            source_account=source_account,
            customer=customer,
            recipient_account=recipient_account,
        )

    def get_last_customer_both_events(
        self,
        source_account_number: str,
        recipient_account_number: str,
        source_customer_id: int | None = None,
        recipient_customer_id: int | None = None,
    ) -> list[CustomerEvent]:
        if source_customer_id is not None or recipient_customer_id is not None:
            customer_ids = [cid for cid in (source_customer_id, recipient_customer_id) if cid is not None]
        else:
            statement = (
                select(Account.customer_id)
                .where(col(Account.account_number).in_([source_account_number, recipient_account_number]))
            )
            customer_ids = [cid for cid in self.session.exec(statement).scalars().all() if cid is not None]

        if not customer_ids:
            return []

        ranked_query = (
            select(
                CustomerEvent.id.label("event_id"),
                func.row_number()
                .over(
                    partition_by=(CustomerEvent.customer_id, CustomerEvent.event_type),
                    order_by=CustomerEvent.occurred_at.desc(),
                )
                .label("rank"),
            )
            .where(col(CustomerEvent.customer_id).in_(customer_ids))
        )

        ranked = ranked_query.subquery()
        statement = (
            select(CustomerEvent)
            .join(ranked, ranked.c.event_id == CustomerEvent.id)
            .where(ranked.c.rank == 1)
        )
        return list(self.session.exec(statement).scalars().all())

    def get_last_transaction(self, source_account_number: str) -> Transaction | None:
        statement = (
            select(Transaction)
            .where(Transaction.source_account_number == source_account_number)
            .order_by(Transaction.transaction_datetime.desc())
            .limit(1)
        )

        return self.session.exec(statement).scalars().first()

    def get_aggregation_features(
            self,
            source_account_number: str,
            recipient_account_number: str,
            mac_address: str,
            tx_datetime: datetime
    ) -> AggregationFeatures:
        hour = func.extract("hour", Transaction.transaction_datetime)
        transaction_date = datetime.combine(tx_datetime, datetime.min.time())

        statement = (
            select(
                func.count(Transaction.id).filter(
                    Transaction.recipient_account_number == recipient_account_number,
                    Transaction.transaction_datetime > tx_datetime - timedelta(hours=3),
                ),
                func.count(Transaction.id).filter(
                    Transaction.transaction_amount > 10000000,
                    Transaction.transaction_datetime > tx_datetime - timedelta(days=7),
                ),
                func.coalesce(
                    func.max(Transaction.transaction_amount).filter(
                        Transaction.transaction_datetime >= tx_datetime - timedelta(days=30),
                    ), 0
                ),
                func.coalesce(
                    func.stddev_pop(Transaction.transaction_amount).filter(
                        Transaction.transaction_datetime >= tx_datetime - timedelta(days=30),
                    ), 0.0
                ),
                func.coalesce(
                    func.max(Transaction.transaction_amount).filter(
                        Transaction.transaction_datetime >= tx_datetime - timedelta(days=30),
                        hour.between(0, 5),
                    ), 0
                ),
                func.coalesce(
                    func.stddev_pop(Transaction.transaction_amount).filter(
                        Transaction.transaction_datetime >= tx_datetime - timedelta(days=30),
                        hour.between(0, 5),
                    ), 0.0
                ),
                func.count(Transaction.id).filter(
                    Transaction.recipient_account_number == recipient_account_number,
                ),
                func.count(Transaction.id).filter(
                    Transaction.mac_address == mac_address,
                ),
                func.max(Transaction.transaction_datetime).filter(
                    Transaction.channel == "atm",
                ),
                func.max(Transaction.transaction_datetime).filter(
                    Transaction.channel == "others",
                ),
                func.coalesce(
                    func.sum(Transaction.transaction_amount).filter(
                        Transaction.transaction_datetime >= transaction_date,
                        Transaction.transaction_datetime < transaction_date + timedelta(days=1),
                    ), 0
                )
            ).where(Transaction.source_account_number == source_account_number)
        )

        aggregation = self.session.exec(statement).one()
        return AggregationFeatures(*aggregation)


__all__ = [
    "FeatureContext",
    "FeatureContextRepository",
]
