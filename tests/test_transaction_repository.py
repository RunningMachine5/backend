import unittest
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.dto.ml_features import MLTransactionFeatures
from app.dto.transaction import TransactionRequestDTO
from app.repositories.transaction import TransactionRepository
from tests.ml_feature_fixture import valid_ml_raw_data


def _payload(**overrides: object) -> TransactionRequestDTO:
    data: dict[str, object] = {
        "customer_id": "C-REPOSITORY",
        "source_account_number": "source-0001",
        "recipient_account_number": "recipient-0001",
        "transaction_datetime": "2026-08-14T12:00:00+09:00",
        "transaction_amount": -10_000,
        "channel": "mobile",
        "type_general_automatic": "general",
        "access_medium": "a",
        "num_connection_failure": 0,
        "operating_system": "android",
        "ip_address": None,
        "mac_address": None,
        "location_lat": 37.5,
        "location_lon": 127.0,
    }
    data.update(overrides)
    return TransactionRequestDTO.model_validate(data)


def _customer(customer_id: str) -> Customer:
    return Customer(
        id=customer_id,
        name=f"고객-{customer_id}",
        birth_date=datetime(1990, 1, 1, tzinfo=UTC),
        gender="female",
        identification_number=f"IDENTITY-{customer_id}",
        registration_datetime=datetime(2020, 1, 1, tzinfo=UTC),
        credit_rating=5,
        loan_type="b",
    )


def _features(payload: TransactionRequestDTO) -> MLTransactionFeatures:
    data = valid_ml_raw_data()
    data.update(
        {
            "transaction_datetime": payload.transaction_datetime,
            "transaction_amount": payload.transaction_amount,
            "channel": payload.channel,
            "operating_system": payload.operating_system,
            "type_general_automatic": payload.type_general_automatic,
            "access_medium": payload.access_medium,
            "transaction_num_connection_failure": (
                payload.num_connection_failure
            ),
        }
    )
    return MLTransactionFeatures.model_validate(data)


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
        self.session = Session(self.engine)
        self.session.add(_customer("C-REPOSITORY"))
        self.session.flush()
        self.session.add_all(
            [
                Account(
                    id="source-0001",
                    customer_id="C-REPOSITORY",
                    account_number="source-0001",
                    account_type="a",
                    creation_datetime=datetime(2020, 1, 1, tzinfo=UTC),
                    amount_daily_limit=3_000_000,
                    indicator_openbanking=True,
                    indicator_release_limit_excess=False,
                    current_balance=10_000_000,
                    remaining_daily_limit=2_000_000,
                ),
                Account(
                    id="recipient-0001",
                    customer_id=None,
                    account_number="recipient-0001",
                ),
            ]
        )
        self.session.commit()
        self.repository = TransactionRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _save(self, payload: TransactionRequestDTO) -> Transaction:
        transaction = self.repository.add_received(payload, _features(payload))
        self.session.commit()
        self.session.refresh(transaction)
        return transaction

    def test_slim_request_saves_transaction_and_account_identifiers(self) -> None:
        transaction = self._save(_payload(operating_system="iOS"))

        self.assertIsInstance(transaction.id, int)
        self.assertEqual(transaction.transaction_amount, -10_000)
        self.assertEqual(transaction.operating_system, "iOS")
        source = self.session.exec(
            select(Account).where(Account.account_number == "source-0001")
        ).one()
        recipient = self.session.exec(
            select(Account).where(Account.account_number == "recipient-0001")
        ).one()
        self.assertEqual(source.customer_id, "C-REPOSITORY")
        self.assertIsNone(recipient.customer_id)
        derived = self.session.get(DerivedFeatures, transaction.id)
        self.assertIsNotNone(derived)
        assert derived is not None
        self.assertEqual(derived.distance, 1.5)
        self.assertEqual(derived.time_difference.total_seconds(), 90)
        self.assertEqual(derived.one_month_max_amount, 500_000)
        self.assertIs(derived.unused_terminal_status, False)

        features = self.repository.load_ml_features(transaction)
        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.account_account_type, "a")
        self.assertEqual(features.account_amount_daily_limit, 3_000_000)
        self.assertEqual(features.operating_system, "iOS")
        self.assertEqual(
            features.account_creation_datetime.replace(tzinfo=UTC),
            datetime(2020, 1, 1, tzinfo=UTC),
        )

    def test_existing_account_uses_latest_transaction_customer(self) -> None:
        self.session.add(_customer("C-OTHER"))
        self.session.flush()
        source = self.session.get(Account, "source-0001")
        assert source is not None
        source.customer_id = "C-OTHER"
        self.session.add(source)
        self.session.commit()

        transaction = self._save(_payload())
        source = self.session.get(Account, "source-0001")

        self.assertEqual(transaction.customer_id, "C-OTHER")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.customer_id, "C-OTHER")

    def test_each_saved_transaction_gets_next_integer_id(self) -> None:
        first = self._save(_payload())
        self.session.add_all(
            [
                Account(
                    id="source-0002",
                    customer_id="C-REPOSITORY",
                    account_number="source-0002",
                ),
                Account(
                    id="recipient-0002",
                    customer_id=None,
                    account_number="recipient-0002",
                ),
            ]
        )
        self.session.commit()
        second = self._save(
            _payload(
                source_account_number="source-0002",
                recipient_account_number="recipient-0002",
            )
        )

        self.assertEqual(second.id, first.id + 1)

    def test_fractional_dawn_std_dev_is_preserved_in_ml_features(self) -> None:
        transaction = self._save(_payload())
        derived = self.session.get(DerivedFeatures, transaction.id)
        self.assertIsNotNone(derived)
        assert derived is not None
        derived.dawn_one_month_std_dev = 12_345.67
        self.session.add(derived)
        self.session.commit()

        features = self.repository.load_ml_features(transaction)

        self.assertEqual(features.account_dawn_one_month_std_dev, 12_345.67)
        self.assertIsInstance(features.account_dawn_one_month_std_dev, float)


if __name__ == "__main__":
    unittest.main()
