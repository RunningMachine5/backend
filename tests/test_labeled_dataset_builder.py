import csv
import unittest
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
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
from tests.ml_feature_fixture import valid_ml_raw_data, valid_transaction_row

FLAG_DEPOSIT_ALIAS = "flag_deposit_more_than_tenmillion"
FLAG_DEPOSIT_CANONICAL = "flag_deposit_more_than_ten_million"
TEST_PERIOD_START = date(2026, 8, 1)
TEST_PERIOD_END = date(2026, 8, 31)


class FakeObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def download(self, uri: str, destination: Path) -> None:
        destination.write_bytes(self.objects[uri])

    def upload_new(self, source: Path, uri: str) -> None:
        if uri in self.objects:
            raise AssertionError("test storage does not allow overwrites")
        self.objects[uri] = source.read_bytes()

    def delete(self, uri: str) -> None:
        self.objects.pop(uri, None)


def _csv_bytes(
    rows: list[dict[str, object]],
    *,
    fieldnames: tuple[str, ...] = TRAINING_CSV_COLUMNS,
) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _training_row(
    transaction_id: str,
    *,
    confirmed_is_fraud: bool,
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        **valid_ml_raw_data(),
        "is_fraud": confirmed_is_fraud,
    }


@dataclass(frozen=True)
class StoredTransactionFixture:
    transaction_id: int
    customer_id: int
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
    customer_id: int,
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
            id=payload.transaction_id * 2 - 1,
            customer_id=customer.id,
            account_number=payload.source_account_number,
            **build_account_fields(features),
        )
        recipient = Account(
            id=payload.transaction_id * 2,
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

    def test_appends_exact_ml_training_row_with_one_join_query(self) -> None:
        long_source = "SOURCE-" + "1" * 80
        long_recipient = "RECIPIENT-" + "1" * 80
        first = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number=long_source,
            recipient_account_number=long_recipient,
            confirmed_is_fraud=True,
        )
        second = _transaction_payload(
            "TX-DATASET-2",
            customer_id=2,
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
                period_start=TEST_PERIOD_START,
                period_end=TEST_PERIOD_END,
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
        self.assertEqual(len(first_row), 53)
        self.assertEqual(first_row["transaction_id"], "1")
        self.assertEqual(first_row["transaction_amount"], "75000")
        self.assertEqual(first_row["time_difference"], "0 days 00:01:30")
        self.assertEqual(
            first_row["customer_flag_change_of_authentication_1"],
            str(first.features.customer_flag_change_of_authentication_1),
        )
        self.assertIn(FLAG_DEPOSIT_CANONICAL, first_row)
        self.assertNotIn(FLAG_DEPOSIT_ALIAS, first_row)
        self.assertEqual(first_row["is_fraud"], "1")
        self.assertEqual(result.normal_count, 1)
        self.assertEqual(result.fraud_count, 1)

    def test_uses_only_confirmed_labels_in_selected_period(self) -> None:
        august = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        september = _transaction_payload(
            "TX-DATASET-2",
            customer_id=2,
            source_account_number="source-account-2",
            recipient_account_number="recipient-account-2",
            confirmed_is_fraud=False,
        )
        self._save(august)
        self._save(september)
        september_transaction = self.session.get(Transaction, september.transaction_id)
        assert september_transaction is not None
        september_transaction.transaction_datetime = datetime(2026, 9, 1)
        self.session.add(september_transaction)
        self.session.commit()

        source_uri = "gs://bucket/base/train1.csv"
        destination_uri = "gs://bucket/versions/august.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([])})
        builder = LabeledDatasetBuilder(storage, source_uri=source_uri)

        summary = builder.label_summary(
            self.session,
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )
        result = builder.build(
            self.session,
            destination_uri=destination_uri,
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )

        self.assertEqual(summary.labeled_count, 1)
        self.assertEqual(summary.normal_count, 0)
        self.assertEqual(summary.fraud_count, 1)
        self.assertEqual(result.appended_label_count, 1)
        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual([row["transaction_id"] for row in rows], ["1"])

    def test_rejects_period_already_included_in_base_dataset(self) -> None:
        builder = LabeledDatasetBuilder(FakeObjectStorage({}))

        with self.assertRaisesRegex(DatasetBuildError, "2026-08-01"):
            builder.label_summary(
                self.session,
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )

    def test_deletes_generated_dataset_but_keeps_base_dataset(self) -> None:
        source_uri = "gs://bucket/base/train1.csv"
        generated_uri = "gs://bucket/versions/august.csv"
        storage = FakeObjectStorage(
            {
                source_uri: _csv_bytes([]),
                generated_uri: _csv_bytes([]),
            }
        )
        builder = LabeledDatasetBuilder(storage, source_uri=source_uri)

        builder.delete_dataset(generated_uri)

        self.assertNotIn(generated_uri, storage.objects)
        self.assertIn(source_uri, storage.objects)
        with self.assertRaisesRegex(DatasetBuildError, "기본 학습"):
            builder.delete_dataset(source_uri)

    def test_preserves_source_row_and_appends_confirmed_db_row(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="stored-source-account",
            recipient_account_number="stored-recipient-account",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        source_row = _training_row(
            payload.transaction_id,
            confirmed_is_fraud=False,
        )
        source_row["account_balance"] = 12345
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        destination_uri = "gs://bucket/generated/v2/transactions.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([source_row])})

        result = LabeledDatasetBuilder(
            storage,
            source_uri=source_uri,
        ).build(
            self.session,
            destination_uri=destination_uri,
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )

        self.assertEqual(result.source_row_count, 1)
        self.assertEqual(result.confirmed_label_count, 1)
        self.assertEqual(result.appended_label_count, 1)
        self.assertEqual(result.output_row_count, 2)
        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(rows[0]["transaction_id"], "1")
        self.assertEqual(rows[0]["is_fraud"], "False")
        self.assertEqual(rows[0]["account_balance"], "12345")
        self.assertEqual(rows[1]["transaction_id"], "1")
        self.assertEqual(rows[1]["is_fraud"], "1")

    def test_reorders_current_53_column_training_source(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="stored-source-account",
            recipient_account_number="stored-recipient-account",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        source_row = {
            "transaction_id": str(payload.transaction_id),
            **valid_ml_raw_data(),
            "is_fraud": 0,
        }
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        destination_uri = "gs://bucket/generated/v2/transactions.csv"
        source_columns = list(TRAINING_CSV_COLUMNS)
        source_columns.remove("customer_loan_type")
        source_columns.insert(
            source_columns.index("customer_credit_rating") + 1,
            "customer_loan_type",
        )
        storage = FakeObjectStorage(
            {
                source_uri: _csv_bytes(
                    [source_row],
                    fieldnames=tuple(source_columns),
                )
            }
        )

        result = LabeledDatasetBuilder(
            storage,
            source_uri=source_uri,
        ).build(
            self.session,
            destination_uri=destination_uri,
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )

        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(result.source_row_count, 1)
        self.assertEqual(result.confirmed_label_count, 1)
        self.assertEqual(result.appended_label_count, 1)
        self.assertEqual(result.output_row_count, 2)
        self.assertEqual(tuple(rows[0]), TRAINING_CSV_COLUMNS)
        self.assertEqual(len(rows[0]), 53)
        self.assertEqual(rows[0]["recipient_release_suspension"], "False")
        self.assertEqual(rows[0][FLAG_DEPOSIT_CANONICAL], "True")
        self.assertEqual(rows[0]["recipient_transaction_resumed_date"], "")

    def test_declined_transaction_keeps_ml_account_balance_in_dataset(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        self._save(payload)

        # 거절된 거래는 실제 계좌 잔액이 빠지지 않으므로 저장 잔액을 원복한다.
        transaction = self.session.get(Transaction, payload.transaction_id)
        assert transaction is not None
        transaction.balance = transaction.initial_balance
        self.session.add(transaction)
        self.session.commit()

        source_uri = "gs://bucket/generated/v1/train1.csv"
        destination_uri = "gs://bucket/generated/v2/train1.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([])})

        LabeledDatasetBuilder(storage, source_uri=source_uri).build(
            self.session,
            destination_uri=destination_uri,
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )

        row = next(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        expected_balance = (
            payload.features.account_initial_balance
            - payload.features.transaction_amount
        )
        self.assertEqual(int(row["account_balance"]), expected_balance)

    def test_zero_initial_balance_is_preserved(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
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
            period_start=TEST_PERIOD_START,
            period_end=TEST_PERIOD_END,
        )

        row = next(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(row["transaction_amount"], "75000")
        self.assertEqual(row["account_initial_balance"], "0")

    def test_reports_feature_contract_validation_as_dataset_build_error(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        source_uri = "gs://bucket/generated/v1/train1.csv"
        destination_uri = "gs://bucket/generated/v2/train1.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([])})

        with self.assertRaises(ValidationError) as caught:
            MLTransactionFeatures.model_validate({})

        with (
            patch(
                "app.services.mlops.dataset_builder.assemble_ml_features",
                side_effect=caught.exception,
            ),
            self.assertRaisesRegex(DatasetBuildError, "학습 계약과 맞지"),
        ):
            LabeledDatasetBuilder(
                storage,
                source_uri=source_uri,
            ).build(
                self.session,
                destination_uri=destination_uri,
                period_start=TEST_PERIOD_START,
                period_end=TEST_PERIOD_END,
            )

        self.assertNotIn(destination_uri, storage.objects)

    def test_rejects_incomplete_source_contract(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id=1,
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        incomplete_columns = [
            column
            for column in TRAINING_CSV_COLUMNS
            if column != "recipient_release_suspension"
        ]
        output = StringIO(newline="")
        csv.DictWriter(output, fieldnames=incomplete_columns).writeheader()
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        storage = FakeObjectStorage({source_uri: output.getvalue().encode("utf-8")})

        with self.assertRaisesRegex(DatasetBuildError, "53열"):
            LabeledDatasetBuilder(
                storage,
                source_uri=source_uri,
            ).build(
                self.session,
                destination_uri="gs://bucket/generated/v2/transactions.csv",
                period_start=TEST_PERIOD_START,
                period_end=TEST_PERIOD_END,
            )


if __name__ == "__main__":
    unittest.main()
