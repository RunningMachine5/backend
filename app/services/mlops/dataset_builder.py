"""확정 라벨을 기존 원본 CSV에 반영해 새 학습 데이터셋을 만든다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Protocol
from urllib.parse import quote, urlsplit

from fastapi import Depends
from google import auth as google_auth
from google.auth.transport.requests import AuthorizedSession
from pydantic import ValidationError
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_prediction import (
    RAW_TRANSACTION_FEATURE_COLUMNS,
    MLTransactionFeatures,
)
from app.services.features.ml_feature_assembler import (
    FeatureAssemblyError,
    assemble_ml_features,
)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CSV_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CSV_DATETIME_COLUMNS = frozenset(
    {
        "Customer_registration_datetime",
        "Account_creation_datetime",
        "Transaction_Datetime",
        "Last_atm_transaction_datetime",
        "Last_bank_branch_transaction_datetime",
        "Transaction_resumed_date",
    }
)


class DatasetBuildError(RuntimeError):
    """입력 데이터 계약이나 병합 조건이 올바르지 않을 때 발생한다."""


class DatasetStorageError(DatasetBuildError):
    """GCS 원본 다운로드 또는 새 객체 업로드 실패."""


@dataclass(frozen=True)
class GCSObject:
    bucket: str
    name: str


def parse_gcs_uri(uri: str) -> GCSObject:
    parsed = urlsplit(uri)
    name = parsed.path.lstrip("/")
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or not name
        or parsed.query
        or parsed.fragment
    ):
        raise DatasetBuildError("GCS URI는 gs://bucket/object 형식이어야 합니다.")
    return GCSObject(bucket=parsed.netloc, name=name)


class ObjectStorage(Protocol):
    def download(self, uri: str, destination: Path) -> None: ...

    def upload_new(self, source: Path, uri: str) -> None: ...


class GCSObjectStorage:
    """ADC와 GCS JSON API를 사용하며 목적 객체 덮어쓰기를 금지한다."""

    def __init__(self) -> None:
        credentials, _ = google_auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        self._session = AuthorizedSession(credentials)

    def download(self, uri: str, destination: Path) -> None:
        target = parse_gcs_uri(uri)
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{quote(target.bucket, safe='')}/o/{quote(target.name, safe='')}"
        )
        response = None
        try:
            response = self._session.get(
                url,
                params={"alt": "media"},
                stream=True,
                timeout=(10, 300),
            )
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        except Exception as exc:
            raise DatasetStorageError(
                f"기존 학습 데이터셋을 다운로드하지 못했습니다: {uri}"
            ) from exc
        finally:
            if response is not None:
                response.close()

    def upload_new(self, source: Path, uri: str) -> None:
        target = parse_gcs_uri(uri)
        url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{quote(target.bucket, safe='')}/o"
        )
        response = None
        try:
            with source.open("rb") as payload:
                response = self._session.post(
                    url,
                    params={
                        "uploadType": "media",
                        "name": target.name,
                        "ifGenerationMatch": "0",
                    },
                    headers={"Content-Type": "text/csv; charset=utf-8"},
                    data=payload,
                    timeout=(10, 600),
                )
            if response.status_code == 412:
                raise DatasetStorageError(f"목적 GCS 객체가 이미 존재합니다: {uri}")
            response.raise_for_status()
        except DatasetStorageError:
            raise
        except Exception as exc:
            raise DatasetStorageError(
                f"새 학습 데이터셋을 업로드하지 못했습니다: {uri}"
            ) from exc
        finally:
            if response is not None:
                response.close()


@dataclass(frozen=True)
class DatasetBuildResult:
    source_row_count: int
    output_row_count: int
    confirmed_label_count: int
    replaced_label_count: int
    appended_label_count: int


@dataclass(frozen=True)
class ConfirmedTransaction:
    """학습 CSV 한 행을 복원하는 데 필요한 최신 ERD의 조인 결과."""

    transaction: Transaction
    label: TransactionLabel
    customer: Customer
    source_account: Account
    recipient_account: Account | None
    derived: DerivedFeatures | None


class LabeledDatasetBuilder:
    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    @staticmethod
    def _confirmed_transactions(
        session: Session,
    ) -> dict[str, ConfirmedTransaction]:
        source_account = aliased(Account, name="source_account")
        recipient_account = aliased(Account, name="recipient_account")
        rows = session.exec(
            select(
                Transaction,
                TransactionLabel,
                Customer,
                source_account,
                recipient_account,
                DerivedFeatures,
            )
            .join(
                TransactionLabel,
                TransactionLabel.transaction_id == Transaction.transaction_id,
            )
            .join(Customer, Customer.customer_id == Transaction.customer_id)
            .join(
                source_account,
                source_account.account_id == Transaction.source_account_id,
            )
            .outerjoin(
                recipient_account,
                recipient_account.account_id == Transaction.recipient_account_id,
            )
            .outerjoin(
                DerivedFeatures,
                DerivedFeatures.transaction_id == Transaction.transaction_id,
            )
            .order_by(TransactionLabel.labeled_at, Transaction.transaction_id)
        ).all()
        return {
            transaction.transaction_id: ConfirmedTransaction(
                transaction=transaction,
                label=label,
                customer=customer,
                source_account=source,
                recipient_account=recipient,
                derived=derived,
            )
            for transaction, label, customer, source, recipient, derived in rows
        }

    @staticmethod
    def _validate_header(fieldnames: list[str] | None) -> list[str]:
        if not fieldnames:
            raise DatasetBuildError("기존 학습 CSV에 헤더가 없습니다.")
        required = {
            "ID",
            "Customer_ID",
            "Customer_personal_identifier",
            "Customer_identification_number",
            "Account_account_number",
            "IP_Address",
            "MAC_Address",
            "Recipient_Account_Number",
            "Is_Fraud",
            *RAW_TRANSACTION_FEATURE_COLUMNS,
        }
        missing = sorted(required - set(fieldnames))
        if missing:
            raise DatasetBuildError(f"기존 학습 CSV에 필수 컬럼이 없습니다: {missing}")
        return fieldnames

    @staticmethod
    def _csv_feature_value(field_name: str, value: object) -> object:
        """새 행의 날짜를 기존 학습 CSV와 동일한 형식으로 직렬화한다."""

        if value is None:
            return ""
        if field_name not in CSV_DATETIME_COLUMNS:
            return value
        if not isinstance(value, datetime):
            raise DatasetBuildError(
                f"확정 라벨 거래의 {field_name} 값이 datetime이 아닙니다."
            )
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.strftime(CSV_DATETIME_FORMAT)

    @staticmethod
    def _new_row(
        fieldnames: list[str],
        confirmed: ConfirmedTransaction,
        assembled: MLTransactionFeatures,
    ) -> dict[str, object]:
        transaction = confirmed.transaction
        try:
            features = assembled.model_dump(mode="python", by_alias=True)
        except ValidationError as exc:
            raise DatasetBuildError(
                "확정 라벨 거래의 원본 Feature가 학습 계약과 맞지 않습니다: "
                f"{transaction.transaction_id}"
            ) from exc

        row: dict[str, object] = {name: "" for name in fieldnames}
        row.update(
            {
                field_name: LabeledDatasetBuilder._csv_feature_value(
                    field_name,
                    value,
                )
                for field_name, value in features.items()
            }
        )
        row.update(
            {
                "ID": transaction.transaction_id,
                "Customer_ID": transaction.customer_id,
                "Customer_personal_identifier": (
                    confirmed.customer.personal_identifier
                ),
                "Customer_identification_number": (
                    confirmed.customer.identification_number
                ),
                "Account_account_number": (confirmed.source_account.account_number),
                "IP_Address": (
                    str(transaction.ip_address)
                    if transaction.ip_address is not None
                    else ""
                ),
                "MAC_Address": (
                    str(transaction.mac_address)
                    if transaction.mac_address is not None
                    else ""
                ),
                "Recipient_Account_Number": (
                    confirmed.recipient_account.account_number
                    if confirmed.recipient_account is not None
                    else ""
                ),
                "Is_Fraud": int(confirmed.label.confirmed_is_fraud),
            }
        )
        return row

    def build(
        self,
        session: Session,
        *,
        source_uri: str,
        destination_uri: str,
    ) -> DatasetBuildResult:
        if parse_gcs_uri(source_uri) == parse_gcs_uri(destination_uri):
            raise DatasetBuildError("새 데이터셋은 기존 GCS 객체와 달라야 합니다.")

        confirmed = self._confirmed_transactions(session)
        if not confirmed:
            raise DatasetBuildError("반영할 확정 거래 라벨이 없습니다.")

        with TemporaryDirectory(prefix="fdshield-dataset-") as temp_directory:
            source_path = Path(temp_directory) / "source.csv"
            output_path = Path(temp_directory) / "output.csv"
            self._storage.download(source_uri, source_path)

            source_row_count = 0
            replaced_label_count = 0
            seen_transaction_ids: set[str] = set()

            with (
                source_path.open("r", encoding="utf-8-sig", newline="") as source_file,
                output_path.open("w", encoding="utf-8", newline="") as output_file,
            ):
                reader = csv.DictReader(source_file)
                fieldnames = self._validate_header(reader.fieldnames)
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=fieldnames,
                    extrasaction="ignore",
                )
                writer.writeheader()

                for row in reader:
                    transaction_id = (row.get("ID") or "").strip()
                    if not transaction_id:
                        raise DatasetBuildError(
                            "기존 학습 CSV에 ID가 비어 있는 행이 있습니다."
                        )
                    if transaction_id in seen_transaction_ids:
                        raise DatasetBuildError(
                            f"기존 학습 CSV에 중복 ID가 있습니다: {transaction_id}"
                        )
                    seen_transaction_ids.add(transaction_id)
                    source_row_count += 1

                    labeled = confirmed.pop(transaction_id, None)
                    if labeled is not None:
                        row["Is_Fraud"] = str(int(labeled.label.confirmed_is_fraud))
                        replaced_label_count += 1
                    writer.writerow(row)

                for transaction_id in sorted(confirmed):
                    labeled = confirmed[transaction_id]
                    if labeled.derived is None:
                        raise DatasetBuildError(
                            "확정 라벨 거래의 파생 피처가 없어 학습 행을 만들 수 "
                            f"없습니다: {transaction_id}"
                        )
                    try:
                        assembled = assemble_ml_features(
                            customer=labeled.customer,
                            source_account=labeled.source_account,
                            transaction=labeled.transaction,
                            derived=labeled.derived,
                        )
                    except (FeatureAssemblyError, ValidationError) as exc:
                        raise DatasetBuildError(
                            "확정 라벨 거래의 원본 Feature가 학습 계약과 맞지 "
                            f"않습니다: {transaction_id}"
                        ) from exc
                    writer.writerow(
                        self._new_row(
                            fieldnames,
                            labeled,
                            assembled,
                        )
                    )

            appended_label_count = len(confirmed)
            output_row_count = source_row_count + appended_label_count
            self._storage.upload_new(output_path, destination_uri)

        return DatasetBuildResult(
            source_row_count=source_row_count,
            output_row_count=output_row_count,
            confirmed_label_count=(replaced_label_count + appended_label_count),
            replaced_label_count=replaced_label_count,
            appended_label_count=appended_label_count,
        )


@lru_cache
def get_labeled_dataset_builder() -> LabeledDatasetBuilder:
    return LabeledDatasetBuilder(GCSObjectStorage())


LabeledDatasetBuilderDep = Annotated[
    LabeledDatasetBuilder,
    Depends(get_labeled_dataset_builder),
]


__all__ = [
    "ConfirmedTransaction",
    "DatasetBuildError",
    "DatasetBuildResult",
    "DatasetStorageError",
    "GCSObjectStorage",
    "LabeledDatasetBuilder",
    "LabeledDatasetBuilderDep",
    "get_labeled_dataset_builder",
    "parse_gcs_uri",
]
