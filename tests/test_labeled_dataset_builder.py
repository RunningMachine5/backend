import csv
import unittest
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_features import RAW_TRANSACTION_FEATURE_COLUMNS, MLTransactionFeatures
from app.services.features.ml_feature_assembler import (
    build_account_fields,
    build_customer_fields,
    build_derived_features_fields,
    build_transaction_fields,
)
from app.services.mlops.dataset_builder import (
    TRAINING_CSV_COLUMNS,
    DatasetBuildError,
    LabeledDatasetBuilder,
)
from tests.ml_feature_fixture import valid_transaction_row

FLAG_DEPOSIT_ALIAS = "flag_deposit_more_than_tenmillion"
FLAG_DEPOSIT_CANONICAL = "flag_deposit_more_than_ten_million"


class FakeObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def download(self, uri: str, destination: Path) -> None:
        destination.write_bytes(self.objects[uri])

    def upload_new(self, source: Path, uri: str) -> None:
        if uri in self.objects:
            raise AssertionError("test storage does not allow overwrites")
        self.objects[uri] = source.read_bytes()


def _csv_bytes(rows: list[dict[str, object]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=TRAINING_CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _training_row(
    transaction_id: str,
    *,
    confirmed_is_fraud: bool,
) -> dict[str, object]:
    row = valid_transaction_row(
        transaction_id,
        is_fraud=confirmed_is_fraud,
    )
    row[FLAG_DEPOSIT_ALIAS] = row.pop(FLAG_DEPOSIT_CANONICAL)
    return row


@dataclass(frozen=True)
class StoredTransactionFixture:
    transaction_id: int
    customer_id: str
    identification_number: str
    source_account_number: str
    recipient_account_number: str
    confirmed_is_fraud: bool
    customer_name: str
    ip_address: str
    mac_address: str
    features: MLTransactionFeatures


def _transaction_payload(
    transaction_id: str,
    *,
    customer_id: str,
    source_account_number: str,
    recipient_account_number: str,
    confirmed_is_fraud: bool,
    initial_balance: int = 10_000_000,
) -> StoredTransactionFixture:
    sequence = transaction_id.rsplit("-", maxsplit=1)[-1]
    row = valid_transaction_row(
        transaction_id,
        is_fraud=confirmed_is_fraud,
    )
    row.update(
        {
            "customer_id": customer_id,
            "customer_name": f"테스트고객-{sequence}",
            "customer_identification_number": f"identity-{sequence}",
            "account_account_number": source_account_number,
            "recipient_account_number": recipient_account_number,
            "account_initial_balance": initial_balance,
            "ip_address": f"2001:db8::{int(sequence)}",
            "mac_address": f"AA-BB-CC-DD-EE-{int(sequence):02X}",
            "is_fraud": confirmed_is_fraud,
        }
    )
    raw51 = {
        column: row[column]
        for column in RAW_TRANSACTION_FEATURE_COLUMNS
        if column
        not in {
            "recipient_release_suspension",
            "recipient_transaction_resumed_date",
        }
    }
    raw51["recipient_release_suspension"] = row[
        "account_release_suspention"
    ]
    raw51["recipient_transaction_resumed_date"] = row[
        "transaction_resumed_date"
    ]
    features = MLTransactionFeatures.model_validate(raw51)
    return StoredTransactionFixture(
        transaction_id=int(sequence),
        customer_id=customer_id,
        identification_number=f"identity-{sequence}",
        source_account_number=source_account_number,
        recipient_account_number=recipient_account_number,
        confirmed_is_fraud=confirmed_is_fraud,
        customer_name=f"테스트고객-{sequence}",
        ip_address=f"2001:db8::{int(sequence)}",
        mac_address=f"AA-BB-CC-DD-EE-{int(sequence):02X}",
        features=features,
    )


class LabeledDatasetBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        DerivedFeatures.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _save(self, payload: StoredTransactionFixture) -> None:
        features = payload.features
        customer = Customer(
            id=payload.customer_id,
            name=payload.customer_name,
            identification_number=payload.identification_number,
            **build_customer_fields(features),
        )
        source = Account(
            id=f"SOURCE-{payload.transaction_id}",
            customer_id=customer.id,
            account_number=payload.source_account_number,
            **build_account_fields(features),
        )
        recipient = Account(
            id=f"RECIPIENT-{payload.transaction_id}",
            account_number=payload.recipient_account_number,
        )
        self.session.add(customer)
        self.session.flush()
        self.session.add(source)
        self.session.add(recipient)
        self.session.flush()
        transaction = Transaction(
            id=payload.transaction_id,
            customer_id=customer.id,
            source_account_number=source.account_number,
            recipient_account_number=recipient.account_number,
            **{
                **build_transaction_fields(features),
                "ip_address": payload.ip_address,
                "mac_address": payload.mac_address,
            },
        )
        self.session.add(transaction)
        self.session.flush()
        self.session.add(
            DerivedFeatures(
                id=transaction.id,
                **build_derived_features_fields(features),
            )
        )
        self.session.add(
            TransactionLabel(
                transaction_id=transaction.id,
                confirmed_is_fraud=payload.confirmed_is_fraud,
            )
        )
        self.session.commit()

    def test_appends_exact_train1_raw64_row_with_one_join_query(self) -> None:
        long_source = "SOURCE-" + "1" * 80
        long_recipient = "RECIPIENT-" + "1" * 80
        first = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number=long_source,
            recipient_account_number=long_recipient,
            confirmed_is_fraud=True,
        )
        second = _transaction_payload(
            "TX-DATASET-2",
            customer_id="C-DATASET-2",
            source_account_number="source-account-2",
            recipient_account_number="recipient-account-2",
            confirmed_is_fraud=False,
        )
        self._save(first)
        self._save(second)

        source_uri = "gs://bucket/generated/v1/transactions.csv"
        destination_uri = "gs://bucket/generated/v2/transactions.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([])})
        select_statements: list[str] = []

        def record_select(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                select_statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", record_select)
        try:
            result = LabeledDatasetBuilder(
                storage,
                source_uri=source_uri,
            ).build(
                self.session,
                destination_uri=destination_uri,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", record_select)

        self.assertEqual(result.appended_label_count, 2)
        self.assertEqual(len(select_statements), 1)
        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        first_row = rows[0]
        self.assertEqual(tuple(rows[0]), TRAINING_CSV_COLUMNS)
        self.assertEqual(len(first_row), 64)
        self.assertEqual(first_row["transaction_id"], "1")
        self.assertEqual(first_row["customer_id"], "C-DATASET-1")
        self.assertEqual(first_row["customer_name"], "테스트고객-1")
        self.assertEqual(
            first_row["customer_identification_number"],
            "identity-1",
        )
        self.assertEqual(first_row["account_account_number"], long_source)
        self.assertEqual(first_row["recipient_account_number"], long_recipient)
        self.assertEqual(first_row["ip_address"], "2001:db8::1")
        self.assertEqual(first_row["mac_address"], "aa:bb:cc:dd:ee:01")
        self.assertEqual(first_row["transaction_amount"], "75000")
        self.assertEqual(first_row["time_difference"], "0 days 00:01:30")
        self.assertAlmostEqual(
            float(first_row["balance_drain_ratio"]),
            75_000 / 10_000_000,
        )
        self.assertIn(FLAG_DEPOSIT_ALIAS, first_row)
        self.assertNotIn(FLAG_DEPOSIT_CANONICAL, first_row)
        self.assertEqual(first_row["is_fraud"], "1")

    def test_preserves_source_row_and_appends_db_row_when_ids_match(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number="stored-source-account",
            recipient_account_number="stored-recipient-account",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        source_row = _training_row(
            payload.transaction_id,
            confirmed_is_fraud=False,
        )
        source_row["customer_name"] = "source-preserved"
        source_row["account_account_number"] = "source-preserved"
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        destination_uri = "gs://bucket/generated/v2/transactions.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([source_row])})

        result = LabeledDatasetBuilder(
            storage,
            source_uri=source_uri,
        ).build(
            self.session,
            destination_uri=destination_uri,
        )

        self.assertEqual(result.source_row_count, 1)
        self.assertEqual(result.appended_label_count, 1)
        self.assertEqual(result.output_row_count, 2)
        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(rows[0]["transaction_id"], "1")
        self.assertEqual(rows[0]["is_fraud"], "False")
        self.assertEqual(rows[0]["customer_name"], "source-preserved")
        self.assertEqual(rows[0]["account_account_number"], "source-preserved")
        self.assertEqual(rows[1]["transaction_id"], "1")
        self.assertEqual(rows[1]["is_fraud"], "1")
        self.assertEqual(rows[1]["customer_name"], "테스트고객-1")
        self.assertEqual(rows[1]["account_account_number"], "stored-source-account")

    def test_zero_initial_balance_emits_empty_metadata_ratio(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
            initial_balance=0,
        )
        self._save(payload)
        source_uri = "gs://bucket/generated/v1/train1.csv"
        destination_uri = "gs://bucket/generated/v2/train1.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([])})

        LabeledDatasetBuilder(
            storage,
            source_uri=source_uri,
        ).build(
            self.session,
            destination_uri=destination_uri,
        )

        row = next(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(row["transaction_amount"], "75000")
        self.assertEqual(row["account_initial_balance"], "0")
        self.assertEqual(row["balance_drain_ratio"], "")

    def test_requires_exact_ordered_train1_raw64_source_contract(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        incomplete_columns = [
            column
            for column in TRAINING_CSV_COLUMNS
            if column != "customer_identification_number"
        ]
        output = StringIO(newline="")
        csv.DictWriter(output, fieldnames=incomplete_columns).writeheader()
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        storage = FakeObjectStorage({source_uri: output.getvalue().encode("utf-8")})

        with self.assertRaisesRegex(DatasetBuildError, "train1 raw64"):
            LabeledDatasetBuilder(
                storage,
                source_uri=source_uri,
            ).build(
                self.session,
                destination_uri="gs://bucket/generated/v2/transactions.csv",
            )


if __name__ == "__main__":
    unittest.main()
