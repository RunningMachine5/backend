import csv
import unittest
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
from app.dto.ml_prediction import RAW_TRANSACTION_FEATURE_COLUMNS
from app.dto.transaction import TransactionCreateDTO
from app.repositories.transaction import TransactionRepository
from app.services.mlops.dataset_builder import (
    DatasetBuildError,
    LabeledDatasetBuilder,
)
from tests.ml_feature_fixture import valid_ml_raw_data, valid_transaction_row

TRACKING_COLUMNS = (
    "ID",
    "Customer_personal_identifier",
    "Customer_identification_number",
    "Account_account_number",
    "IP_Address",
    "MAC_Address",
    "Recipient_Account_Number",
    "Customer_ID",
)
CSV_COLUMNS = (*TRACKING_COLUMNS, *RAW_TRANSACTION_FEATURE_COLUMNS, "Is_Fraud")


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
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _transaction_payload(
    transaction_id: str,
    *,
    customer_id: str,
    source_account_number: str,
    recipient_account_number: str,
    confirmed_is_fraud: bool,
) -> TransactionCreateDTO:
    sequence = transaction_id.rsplit("-", maxsplit=1)[-1]
    return TransactionCreateDTO.model_validate(
        {
            **valid_transaction_row(transaction_id),
            "Customer_ID": customer_id,
            "Customer_personal_identifier": f"테스트고객-{sequence}",
            "Customer_identification_number": f"identity-{sequence}",
            "Account_account_number": source_account_number,
            "Recipient_Account_Number": recipient_account_number,
            "IP_Address": f"2001:db8::{int(sequence)}",
            "MAC_Address": f"AA-BB-CC-DD-EE-{int(sequence):02X}",
            "Is_Fraud": confirmed_is_fraud,
        }
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

    def _save(self, payload: TransactionCreateDTO) -> None:
        TransactionRepository(self.session).add_received(payload)
        self.session.commit()

    def test_appends_actual_tracking_values_with_one_join_query(self) -> None:
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
            result = LabeledDatasetBuilder(storage).build(
                self.session,
                source_uri=source_uri,
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
        self.assertEqual(first_row["ID"], "TX-DATASET-1")
        self.assertEqual(first_row["Customer_ID"], "C-DATASET-1")
        self.assertEqual(first_row["Customer_personal_identifier"], "테스트고객-1")
        self.assertEqual(first_row["Customer_identification_number"], "identity-1")
        self.assertEqual(first_row["Account_account_number"], long_source)
        self.assertEqual(first_row["Recipient_Account_Number"], long_recipient)
        self.assertEqual(first_row["IP_Address"], "2001:db8::1")
        self.assertEqual(first_row["MAC_Address"], "aa:bb:cc:dd:ee:01")
        self.assertEqual(first_row["Transaction_Amount"], "3995050")
        self.assertEqual(first_row["Is_Fraud"], "1")

    def test_replaces_existing_label_without_changing_source_values(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number="stored-source-account",
            recipient_account_number="stored-recipient-account",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        source_row = {
            **{column: "source-preserved" for column in TRACKING_COLUMNS},
            **valid_ml_raw_data(),
            "ID": payload.transaction_id,
            "Is_Fraud": 0,
        }
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        destination_uri = "gs://bucket/generated/v2/transactions.csv"
        storage = FakeObjectStorage({source_uri: _csv_bytes([source_row])})

        result = LabeledDatasetBuilder(storage).build(
            self.session,
            source_uri=source_uri,
            destination_uri=destination_uri,
        )

        self.assertEqual(result.replaced_label_count, 1)
        self.assertEqual(result.appended_label_count, 0)
        row = next(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(row["Is_Fraud"], "1")
        self.assertEqual(row["Customer_personal_identifier"], "source-preserved")
        self.assertEqual(row["Account_account_number"], "source-preserved")

    def test_requires_complete_63_column_source_contract(self) -> None:
        payload = _transaction_payload(
            "TX-DATASET-1",
            customer_id="C-DATASET-1",
            source_account_number="source-account-1",
            recipient_account_number="recipient-account-1",
            confirmed_is_fraud=True,
        )
        self._save(payload)
        incomplete_columns = [
            "ID",
            "Customer_ID",
            *RAW_TRANSACTION_FEATURE_COLUMNS,
            "Is_Fraud",
        ]
        output = StringIO(newline="")
        csv.DictWriter(output, fieldnames=incomplete_columns).writeheader()
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        storage = FakeObjectStorage({source_uri: output.getvalue().encode("utf-8")})

        with self.assertRaisesRegex(DatasetBuildError, "필수 컬럼"):
            LabeledDatasetBuilder(storage).build(
                self.session,
                source_uri=source_uri,
                destination_uri="gs://bucket/generated/v2/transactions.csv",
            )


if __name__ == "__main__":
    unittest.main()
