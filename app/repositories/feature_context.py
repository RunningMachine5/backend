from datetime import UTC, date, datetime, timedelta, time

from pydantic.dataclasses import dataclass
from sqlalchemy import func
from sqlmodel import Session, select

from app.data.model import Account, Customer, CustomerEvent, Transaction

TEMP_ACCOUNT_ID_PREFIX = "TEMP-ACCOUNT-"
TEMP_CUSTOMER_ID_PREFIX = "TEMP-CUSTOMER-"


@dataclass(frozen=True)
class FeatureContext:
    source_account: Account | None
    customer: Customer | None
    recipient_account: Account | None


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
        customer_id: str | None = None,
    ) -> FeatureContext:
        """실제 원장을 우선 사용하고, 없는 고객·계좌만 임시값으로 보완한다."""

        source_account = self.get_account(account_number)
        if source_account is None:
            source_customer = self._get_or_create_customer(
                customer_id or f"{TEMP_CUSTOMER_ID_PREFIX}{account_number}"
            )
            source_account = Account(
                id=f"{TEMP_ACCOUNT_ID_PREFIX}{account_number}",
                customer_id=source_customer.id,
                account_number=account_number,
            )
            self.session.add(source_account)
        else:
            source_customer_id = (
                source_account.customer_id
                or customer_id
                or f"{TEMP_CUSTOMER_ID_PREFIX}{account_number}"
            )
            source_customer = self._get_or_create_customer(source_customer_id)
            if source_account.customer_id is None:
                source_account.customer_id = source_customer.id

        self._fill_missing_source_account_values(source_account)
        self.session.flush()

        recipient_account = self.get_account(recipient_account_number)
        if recipient_account is None:
            recipient_account = Account(
                id=f"{TEMP_ACCOUNT_ID_PREFIX}{recipient_account_number}",
                customer_id=None,
                account_number=recipient_account_number,
                suspend_status=False,
            )
            self.session.add(recipient_account)
            self.session.flush()

        return FeatureContext(
            source_account = source_account,
            customer = source_customer,
            recipient_account = recipient_account,
        )

    def _get_or_create_customer(self, customer_id: str) -> Customer:
        customer = self.get_customer(customer_id)
        if customer is not None:
            return customer

        now = datetime.now(UTC)
        customer = Customer(
            id=customer_id,
            name="UNKNOWN",
            birth_date=date(1990, 1, 1),
            gender="male",
            identification_number=f"TEMP-{customer_id}",
            registration_datetime=now,
            credit_rating=5,
            loan_type="a",
        )
        self.session.add(customer)
        self.session.flush()
        return customer

    def _fill_missing_source_account_values(self, account: Account) -> None:
        """ML raw51에 필요한 계좌값만 단순 기본값으로 채운다."""

        now = datetime.now(UTC)
        account.account_type = account.account_type or "a"
        account.creation_datetime = account.creation_datetime or now
        account.amount_daily_limit = account.amount_daily_limit or 0
        account.indicator_openbanking = account.indicator_openbanking or False
        account.indicator_release_limit_excess = (
            account.indicator_release_limit_excess or False
        )
        if account.current_balance is None:
            account.current_balance = 0
        if account.remaining_daily_limit is None:
            account.remaining_daily_limit = 0
        self.session.add(account)

    def get_account(self, account_number: str) -> Account | None:
        statement = select(Account).where(Account.account_number == account_number)
        return self.session.exec(statement).first()

    def get_customer(self, customer_id: str | None) -> Customer | None:
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
    "TEMP_ACCOUNT_ID_PREFIX",
    "FeatureContext",
    "FeatureContextRepository",
]
