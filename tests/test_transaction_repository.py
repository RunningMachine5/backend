import unittest

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.transaction import TransactionCreateDTO
from app.repositories.transaction import (
    AccountOwnershipConflictError,
    TransactionRepository,
)
from tests.ml_feature_fixture import valid_transaction_row


def _payload(
    transaction_id: str,
    *,
    customer_id: str = "C000494",
    identification_number: str = "upTALE-VwSUVKY",
    source_account_number: str = "TLBxRCjZdK",
    recipient_account_number: str = "yeTPcVrUhr",
    **overrides: object,
) -> TransactionCreateDTO:
    row = {
        **valid_transaction_row(transaction_id),
        "customer_id": customer_id,
        "customer_identification_number": identification_number,
        "account_account_number": source_account_number,
        "recipient_account_number": recipient_account_number,
        **overrides,
    }
    return TransactionCreateDTO.model_validate(row)


class TransactionRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        DerivedFeatures.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.repository = TransactionRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _save(self, payload: TransactionCreateDTO) -> Transaction:
        transaction = self.repository.add_received(payload)
        self.session.commit()
        self.session.refresh(transaction)
        return transaction

    def test_saved_features_round_trip_exactly_after_account_state_changes(
        self,
    ) -> None:
        first_payload = _payload("TX-ROUNDTRIP-1")
        first_transaction = self._save(first_payload)

        second_payload = _payload(
            "TX-ROUNDTRIP-2",
            account_initial_balance=4_817_417,
            account_balance=-3_000_000,
            account_remaining_amount_daily_limit_exceeded=4_000_000,
            transaction_amount=1_817_417,
        )
        second_transaction = self._save(second_payload)

        first_assembled = self.repository.load_ml_features(first_transaction)
        second_assembled = self.repository.load_ml_features(second_transaction)

        self.assertIsNotNone(first_assembled)
        self.assertIsNotNone(second_assembled)
        self.assertEqual(
            first_assembled.model_dump(mode="json", by_alias=True),
            first_payload.raw_features.model_dump(mode="json", by_alias=True),
        )
        self.assertEqual(
            second_assembled.model_dump(mode="json", by_alias=True),
            second_payload.raw_features.model_dump(mode="json", by_alias=True),
        )
        account = self.session.exec(
            select(Account).where(
                Account.account_number == first_transaction.source_account_number
            )
        ).one()
        self.assertIsNotNone(account)
        self.assertEqual(account.current_balance, -3_000_000)
        self.assertEqual(account.remaining_daily_limit, 4_000_000)

    def test_transaction_is_inserted_before_derived_features(self) -> None:
        insert_order: list[str] = []

        def capture_insert_order(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = statement.lstrip().lower()
            if normalized.startswith("insert into transactions"):
                insert_order.append("transactions")
            elif normalized.startswith("insert into derived_features"):
                insert_order.append("derived_features")

        event.listen(self.engine, "before_cursor_execute", capture_insert_order)
        try:
            self._save(_payload("TX-FK-ORDER"))
        finally:
            event.remove(
                self.engine,
                "before_cursor_execute",
                capture_insert_order,
            )

        self.assertEqual(insert_order, ["transactions", "derived_features"])
        self.assertIsNotNone(self.session.get(Transaction, "TX-FK-ORDER"))
        self.assertIsNotNone(self.session.get(DerivedFeatures, "TX-FK-ORDER"))

    def test_updates_customer_and_account_values_from_latest_payload(self) -> None:
        self._save(_payload("TX-MASTER-1"))

        latest = _payload(
            "TX-MASTER-2",
            customer_credit_rating=5,
            customer_loan_type="d",
            account_amount_daily_limit=20_000_000,
            account_indicator_openbanking=False,
        )
        self._save(latest)

        customer = self.session.get(Customer, latest.customer_id)
        account = self.session.get(Account, latest.source_account_number)
        self.assertIsNotNone(customer)
        self.assertIsNotNone(account)
        self.assertEqual(customer.credit_rating, 5)
        self.assertEqual(customer.loan_type, "d")
        self.assertEqual(account.amount_daily_limit, 20_000_000)
        self.assertFalse(account.indicator_openbanking)

    def test_recipient_account_can_later_be_claimed_by_its_owner(self) -> None:
        target_account = "recipient-becomes-source"
        self._save(
            _payload(
                "TX-RECIPIENT-FIRST",
                recipient_account_number=target_account,
                recipient_account_suspend_status=True,
            )
        )
        recipient = self.session.get(Account, target_account)
        self.assertIsNotNone(recipient)
        self.assertIsNone(recipient.customer_id)
        self.assertTrue(recipient.suspend_status)

        owner_payload = _payload(
            "TX-SOURCE-LATER",
            customer_id="C-OWNER-2",
            identification_number="owner-identity-2",
            source_account_number=target_account,
            recipient_account_number="recipient-owner-2",
            customer_name="계좌주인",
        )
        self._save(owner_payload)

        claimed = self.session.get(Account, target_account)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.customer_id, "C-OWNER-2")
        self.assertEqual(
            claimed.account_type, owner_payload.raw_features.account_account_type
        )

        with self.assertRaises(AccountOwnershipConflictError):
            self.repository.add_received(
                _payload(
                    "TX-WRONG-OWNER",
                    customer_id="C-WRONG-OWNER",
                    identification_number="wrong-owner-identity",
                    source_account_number=target_account,
                    recipient_account_number="recipient-wrong-owner",
                    customer_name="다른고객",
                )
            )
        self.session.rollback()
        self.assertEqual(
            self.session.get(Account, target_account).customer_id,
            "C-OWNER-2",
        )

    def test_recipient_suspend_status_tracks_latest_observation(self) -> None:
        self._save(
            _payload(
                "TX-SUSPEND-1",
                recipient_account_suspend_status=True,
            )
        )
        recipient = self.session.get(Account, "yeTPcVrUhr")
        self.assertIsNotNone(recipient)
        self.assertTrue(recipient.suspend_status)

        self._save(
            _payload(
                "TX-SUSPEND-2",
                recipient_account_suspend_status=False,
            )
        )
        self.assertFalse(self.session.get(Account, "yeTPcVrUhr").suspend_status)


if __name__ == "__main__":
    unittest.main()
