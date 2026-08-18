from datetime import date, datetime, timedelta, time

from pydantic.dataclasses import dataclass
from sqlalchemy import func
from sqlmodel import Session, select

from app.data.model import Account, Customer, CustomerEvent, Transaction

@dataclass(frozen=True)
class FeatureContext:
    source_account: Account
    customer: Customer
    recipient_account: Account


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

        source_account = self.session.exec(
            select(Account).where(Account.account_number == account_number)
        ).one()
        customer = self.session.exec(
            select(Customer).where(Customer.id == source_account.customer_id)
        ).one()
        recipient_account = self.session.exec(
            select(Account).where(
                Account.account_number == recipient_account_number
            )
        ).one()

        return FeatureContext(
            source_account=source_account,
            customer=customer,
            recipient_account=recipient_account,
        )

    def get_account(self, account_number: str) -> Account | None:
        statement = select(Account).where(Account.account_number == account_number)
        return self.session.exec(statement).first()

    def get_customer(self, customer_id: int | None) -> Customer | None:
        statement = select(Customer).where(Customer.id == customer_id)
        return self.session.exec(statement).first()

    def get_last_customer_events(self, source_account_number: str, tx_datetime: datetime, event_timedelta: int | None = None) -> dict[str, CustomerEvent | None]:
        account = self.get_account(source_account_number)
        customer_id = account.customer_id if account is not None else None
        ranked_query = (
            select(
                CustomerEvent.id.label("event_id"),
                func.row_number()
                .over(
                    partition_by=CustomerEvent.event_type,
                    order_by=CustomerEvent.occurred_at.desc(),
                )
                .label("rank"),
            )
            .where(CustomerEvent.customer_id == customer_id)
        )
        if event_timedelta is not None:
            ranked_query = ranked_query.where(CustomerEvent.occurred_at > tx_datetime - timedelta(days=event_timedelta))

        ranked = ranked_query.subquery()
        statement = (
            select(CustomerEvent)
            .join(ranked, ranked.c.event_id == CustomerEvent.id)
            .where(ranked.c.rank == 1)
        )
        events = self.session.exec(statement).all()
        return {event.event_type: event for event in events}

    def get_last_transaction(self, source_account_number: str) -> Transaction | None:
        statement = (
            select(Transaction)
            .where(Transaction.source_account_number == source_account_number)
            .order_by(Transaction.transaction_datetime.desc())
            .limit(1)
        )

        return self.session.exec(statement).first()

    def get_3hours_transaction(self, source_account_number: str, recipient_account_number: str, tx_time: datetime) -> int:
        statement = (
            select(func.count(Transaction.id))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.recipient_account_number == recipient_account_number,
                Transaction.transaction_datetime > tx_time - timedelta(hours=3)
            )
        )

        return self.session.exec(statement).one()

    def get_count_over_ten_million_transactions_for_week(self, source_account_number: str, tx_datetime: datetime) -> int:
        statement = (
            select(func.count(Transaction.id))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.transaction_amount > 10000000,
                Transaction.transaction_datetime > tx_datetime - timedelta(days=7),
            )
        )
        return self.session.exec(statement).one()

    def get_one_month_max_amount(self, source_account_number: str, tx_datetime: datetime, is_dawn: bool) -> int:
        statement = (
            select(func.coalesce(func.max(Transaction.transaction_amount), 0))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.transaction_datetime >= tx_datetime - timedelta(days=30),
            )
        )
        if is_dawn:
            hour = func.extract("hour", Transaction.transaction_datetime)
            statement = statement.where(hour.between(0, 5))

        return self.session.exec(statement).one()

    def get_one_month_std_dev(self, source_account_number: str, txdatetime: datetime, is_dawn: bool) -> float:
        statement = (
            select(func.coalesce(func.stddev_pop(Transaction.transaction_amount), 0))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.transaction_datetime >= txdatetime - timedelta(days=30),
            )
        )
        if is_dawn:
            hour = func.extract("hour", Transaction.transaction_datetime)
            statement = statement.where(hour.between(0, 5))

        return self.session.exec(statement).one()
    
    def get_transaction_history_count(self, source_account_number: str, recipient_account_number: str) -> int:
        statement = (
            select(func.count(Transaction.id))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.recipient_account_number == recipient_account_number,
            )
        )
        return self.session.exec(statement).one()

    def get_mac_address_history_count(self, source_account_number: str, mac_address: str | None) -> int:
        statement = (
            select(func.count(Transaction.id))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.mac_address == mac_address,
            )
        )
        return self.session.exec(statement).one()

    def get_last_transaction_datetime_by_channel(self, source_account_number: str, channel: str) -> datetime | None:
        statement = (
            select(func.max(Transaction.transaction_datetime))
            .where(
                Transaction.source_account_number == source_account_number,
                Transaction.channel == channel,
            )
        )
        return self.session.exec(statement).one()

    def get_todays_transaction_amount(self, account_number: str, transaction_date: date) -> int | None:
        transaction_date = datetime.combine(transaction_date, time.min)
        statement = (
            select(func.coalesce(func.sum(Transaction.transaction_amount), 0))
            .where(Transaction.source_account_number == account_number)
            .where(Transaction.transaction_datetime >= transaction_date)
            .where(Transaction.transaction_datetime < transaction_date + timedelta(days=1))
        )
        return self.session.exec(statement).one()


__all__ = [
    "FeatureContext",
    "FeatureContextRepository",
]
