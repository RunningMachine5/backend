import unittest
from datetime import UTC, datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.repositories.transaction import TransactionRepository


class TransactionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        self.session = Session(self.engine)

        customer = Customer(
            name="테스트 고객",
            birth_date=datetime(1990, 1, 1, tzinfo=UTC).date(),
            gender="female",
            identification_number="TEST-CUSTOMER-1",
            registration_datetime=datetime(2020, 1, 1, tzinfo=UTC),
            credit_rating=5,
            loan_type="b",
        )
        self.session.add(customer)
        self.session.flush()
        self.customer_id = customer.id

        self.session.add_all(
            [
                Account(
                    customer_id=self.customer_id,
                    account_number="source-0001",
                    current_balance=100_000,
                ),
                Account(
                    account_number="recipient-0001",
                ),
            ]
        )
        self.session.commit()
        self.repository = TransactionRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _transaction(self) -> Transaction:
        return Transaction(
            customer_id=self.customer_id,
            source_account_number="source-0001",
            recipient_account_number="recipient-0001",
            transaction_datetime=datetime(2026, 8, 18, tzinfo=UTC),
            transaction_amount=10_000,
            channel="mobile",
            type_general_automatic="general",
            access_medium="a",
            num_connection_failure=0,
            initial_balance=100_000,
            balance=90_000,
            rooting_jailbreak_indicator=False,
            mobile_roaming_indicator=False,
            vpn_indicator=False,
            flag_terminal_malicious_behavior_1=False,
            flag_terminal_malicious_behavior_2=False,
            flag_terminal_malicious_behavior_3=False,
            flag_terminal_malicious_behavior_5=False,
            flag_terminal_malicious_behavior_6=False,
        )

    def test_save_transaction_generates_integer_id(self) -> None:
        stored = self.repository.save_transaction(self._transaction())

        self.assertIsInstance(stored.id, int)
        self.assertIs(self.repository.get(stored.id), stored)

    def test_update_source_balance_changes_current_balance(self) -> None:
        self.repository.update_source_balance("source-0001", 90_000)
        self.session.commit()

        account = self.session.get(Account, 1)
        self.assertIsNotNone(account)
        self.assertEqual(account.current_balance, 90_000)


if __name__ == "__main__":
    unittest.main()
