import csv
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_prediction import RAW_TRANSACTION_FEATURE_COLUMNS
from app.services.mlops.dataset_builder import (
    DatasetBuildError,
    LabeledDatasetBuilder,
)
from tests.ml_feature_fixture import valid_ml_raw_data


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
    fieldnames = [
        "ID",
        "Customer_ID",
        "Account_account_number",
        *RAW_TRANSACTION_FEATURE_COLUMNS,
        "Is_Fraud",
    ]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _transaction(transaction_id: str) -> Transaction:
    raw_features = valid_ml_raw_data()
    return Transaction(
        transaction_id=transaction_id,
        customer_id=f"C-{transaction_id}",
        source_account_id=f"A-{transaction_id}",
        recipient_account_id=None,
        transaction_datetime=datetime.fromisoformat(
            raw_features["Transaction_Datetime"]
        ),
        transaction_amount=raw_features["Transaction_Amount"],
        channel=raw_features["Channel"],
        location=raw_features["Location"],
        raw_features=raw_features,
    )


class LabeledDatasetBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Transaction.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_replaces_existing_label_and_appends_new_transaction(self) -> None:
        existing = _transaction("T-EXISTING")
        appended = _transaction("T-NEW")
        self.session.add_all(
            [
                existing,
                appended,
                TransactionLabel(
                    transaction_id=existing.transaction_id,
                    confirmed_is_fraud=True,
                ),
                TransactionLabel(
                    transaction_id=appended.transaction_id,
                    confirmed_is_fraud=False,
                ),
            ]
        )
        self.session.commit()

        source_row = {
            "ID": existing.transaction_id,
            "Customer_ID": existing.customer_id,
            "Account_account_number": existing.source_account_id,
            **valid_ml_raw_data(),
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

        self.assertEqual(result.source_row_count, 1)
        self.assertEqual(result.output_row_count, 2)
        self.assertEqual(result.confirmed_label_count, 2)
        self.assertEqual(result.replaced_label_count, 1)
        self.assertEqual(result.appended_label_count, 1)

        rows = list(
            csv.DictReader(StringIO(storage.objects[destination_uri].decode("utf-8")))
        )
        self.assertEqual(
            [row["ID"] for row in rows],
            ["T-EXISTING", "T-NEW"],
        )
        self.assertEqual(rows[0]["Is_Fraud"], "1")
        self.assertEqual(rows[1]["Is_Fraud"], "0")
        self.assertEqual(rows[1]["Customer_ID"], "C-T-NEW")
        self.assertEqual(rows[1]["Account_account_number"], "A-T-NEW")
        self.assertEqual(rows[1]["Transaction_Amount"], "3995050")
        self.assertEqual(
            rows[1]["Customer_registration_datetime"],
            "2023-01-20 09:41:55",
        )
        self.assertEqual(
            rows[1]["Account_creation_datetime"],
            "2024-12-02 22:14:22",
        )
        self.assertEqual(
            rows[1]["Transaction_Datetime"],
            "2025-01-01 00:00:00",
        )
        self.assertEqual(rows[1]["Last_atm_transaction_datetime"], "")
        self.assertEqual(rows[1]["Last_bank_branch_transaction_datetime"], "")
        self.assertEqual(rows[1]["Transaction_resumed_date"], "")

    def test_rejects_overwriting_source_object(self) -> None:
        transaction = _transaction("T-1")
        self.session.add(transaction)
        self.session.add(
            TransactionLabel(
                transaction_id=transaction.transaction_id,
                confirmed_is_fraud=True,
            )
        )
        self.session.commit()

        uri = "gs://bucket/generated/v1/transactions.csv"
        builder = LabeledDatasetBuilder(FakeObjectStorage({uri: b"unused"}))

        with self.assertRaisesRegex(
            DatasetBuildError,
            "기존 GCS 객체와 달라야",
        ):
            builder.build(self.session, source_uri=uri, destination_uri=uri)

    def test_requires_confirmed_labels(self) -> None:
        source_uri = "gs://bucket/generated/v1/transactions.csv"
        storage = FakeObjectStorage({source_uri: b"unused"})

        with self.assertRaisesRegex(DatasetBuildError, "확정 거래 라벨"):
            LabeledDatasetBuilder(storage).build(
                self.session,
                source_uri=source_uri,
                destination_uri="gs://bucket/generated/v2/transactions.csv",
            )


if __name__ == "__main__":
    unittest.main()
