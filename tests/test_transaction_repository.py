import unittest
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.dto.transaction import TransactionRequestDTO
from app.repositories.transaction import TransactionRepository


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
        self.session.commit()
        self.repository = TransactionRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _save(self, payload: TransactionRequestDTO) -> Transaction:
        transaction = self.repository.add_received(payload)
        self.session.commit()
        self.session.refresh(transaction)
        return transaction

    def test_slim_request_saves_transaction_and_account_identifiers(self) -> None:
        transaction = self._save(_payload())

        self.assertIsInstance(transaction.id, int)
        self.assertEqual(transaction.transaction_amount, -10_000)
        self.assertEqual(transaction.location, "37.5 127.0")
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
        self.assertEqual(derived.distance, 0.0)
        self.assertEqual(derived.time_difference.total_seconds(), 0)
        self.assertEqual(derived.one_month_max_amount, 0)
        self.assertIs(derived.unused_terminal_status, False)

        # 계좌 상세 프로필이 아직 없어도 DB 원본을 덮지 않고
        # ML 조립 시에만 중립 기본값을 사용한다.
        features = self.repository.load_ml_features(transaction)
        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.account_account_type, "a")
        self.assertEqual(features.account_amount_daily_limit, 0)
        self.assertEqual(
            features.account_creation_datetime,
            transaction.transaction_datetime,
        )

    def test_unknown_customer_uses_temporary_profile(self) -> None:
        transaction = self._save(
            _payload(
                customer_id="C-MISSING",
                recipient_account_number=None,
                channel="ATM",
            )
        )
        self.assertIsNone(transaction.customer_id)
        self.assertIsNone(transaction.recipient_account_number)
        self.assertEqual(transaction.channel, "atm")
        features = self.repository.load_ml_features(transaction)
        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.recipient_account_number, "unknown-recipient")
        self.assertEqual(features.customer_name, "unknown-customer")

    def test_existing_account_uses_latest_transaction_customer(self) -> None:
        self.session.add(_customer("C-OTHER"))
        self.session.flush()
        self.session.add(
            Account(
                id="source-0001",
                customer_id="C-OTHER",
                account_number="source-0001",
            )
        )
        self.session.commit()

        transaction = self._save(_payload())
        source = self.session.get(Account, "source-0001")

        self.assertEqual(transaction.customer_id, "C-REPOSITORY")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.customer_id, "C-REPOSITORY")

    def test_each_saved_transaction_gets_next_integer_id(self) -> None:
        first = self._save(_payload())
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
