"""확정 라벨을 기존 원본 CSV에 반영해 새 학습 데이터셋을 만든다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from app.dto.ml_features import (
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
        "customer_birth_date",
        "customer_registration_datetime",
        "account_creation_datetime",
        "transaction_datetime",
        "last_atm_transaction_datetime",
        "last_bank_branch_transaction_datetime",
        "transaction_resumed_date",
    }
)
CSV_DURATION_COLUMNS = frozenset({"time_difference"})
CSV_INTEGER_AMOUNT_COLUMNS = frozenset(
    {
        "account_initial_balance",
        "account_balance",
        "account_amount_daily_limit",
        "account_remaining_amount_daily_limit_exceeded",
        "account_one_month_max_amount",
        "account_dawn_one_month_max_amount",
        "transaction_amount",
    }
)

TRAINING_TRANSACTION_ID_COLUMN = "transaction_id"
TRAINING_IDENTIFICATION_COLUMN = "customer_identification_number"
TRAINING_CUSTOMER_ID_COLUMN = "customer_id"
TRAINING_BALANCE_DRAIN_RATIO_COLUMN = "balance_drain_ratio"
TRAINING_LABEL_COLUMN = "is_fraud"
TRAINING_FLAG_DEPOSIT_ALIAS = "flag_deposit_more_than_tenmillion"
TRAINING_FLAG_DEPOSIT_CANONICAL = "flag_deposit_more_than_ten_million"

# ML 담당자가 전달한 train1.csv는 raw59에 식별/메타데이터 4개와 라벨을
# 정해진 위치에 끼운 raw64 계약이다. 실제 파일의 known typo 한 개도 헤더
# 호환을 위해 그대로 출력하고 ML loader가 canonical 이름으로 정규화한다.
TRAINING_CSV_COLUMNS = (
    TRAINING_TRANSACTION_ID_COLUMN,
    *RAW_TRANSACTION_FEATURE_COLUMNS[:3],
    TRAINING_IDENTIFICATION_COLUMN,
    *(
        TRAINING_FLAG_DEPOSIT_ALIAS
        if column == TRAINING_FLAG_DEPOSIT_CANONICAL
        else column
        for column in RAW_TRANSACTION_FEATURE_COLUMNS[3:]
    ),
    TRAINING_CUSTOMER_ID_COLUMN,
    TRAINING_BALANCE_DRAIN_RATIO_COLUMN,
    TRAINING_LABEL_COLUMN,
)
if len(TRAINING_CSV_COLUMNS) != 64:  # pragma: no cover - import invariant
    raise RuntimeError("TRAINING_CSV_COLUMNS must contain exactly 64 columns.")
if len(TRAINING_CSV_COLUMNS) != len(  # pragma: no cover - import invariant
    set(TRAINING_CSV_COLUMNS)
):
    raise RuntimeError("TRAINING_CSV_COLUMNS must not contain duplicates.")


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
    """기존 train1 CSV에 확정 라벨 거래를 반영해 새 버전을 만든다.

    기존 GCS 객체는 수정하지 않는다. 같은 거래 ID가 있으면 라벨과 원천 값을
    교체하고, 없으면 DB의 정규화 테이블을 raw64 한 행으로 복원해 추가한다.
    """

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    @staticmethod
    def _confirmed_transactions(
        session: Session,
    ) -> dict[int, ConfirmedTransaction]:
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
                TransactionLabel.transaction_id == Transaction.id,
            )
            .join(Customer, Customer.id == Transaction.customer_id)
            .join(
                source_account,
                source_account.account_number == Transaction.source_account_number,
            )
            .outerjoin(
                recipient_account,
                recipient_account.account_number
                == Transaction.recipient_account_number,
            )
            .outerjoin(
                DerivedFeatures,
                DerivedFeatures.id == Transaction.id,
            )
            .order_by(TransactionLabel.labeled_at, Transaction.id)
        ).all()
        return {
            transaction.id: ConfirmedTransaction(
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
    def _transaction_id(value: str) -> int:
        """train1의 기존 T00000001 형식과 신규 정수 ID를 같은 값으로 본다."""

        normalized = value.strip()
        if normalized[:1].upper() == "T":
            normalized = normalized[1:]
        if not normalized.isdigit() or int(normalized) <= 0:
            raise DatasetBuildError(
                f"기존 학습 CSV의 transaction_id가 올바르지 않습니다: {value}"
            )
        return int(normalized)

    @staticmethod
    def _validate_header(fieldnames: list[str] | None) -> list[str]:
        if not fieldnames:
            raise DatasetBuildError("기존 학습 CSV에 헤더가 없습니다.")
        provided = tuple(fieldnames)
        if provided != TRAINING_CSV_COLUMNS:
            expected = set(TRAINING_CSV_COLUMNS)
            actual = set(provided)
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            duplicates = sorted(
                {column for column in provided if provided.count(column) > 1}
            )
            raise DatasetBuildError(
                "기존 학습 CSV가 train1 raw64 헤더 계약과 다릅니다: "
                f"missing={missing}, unknown={unknown}, "
                f"duplicates={duplicates}, order_matches=False"
            )
        return fieldnames

    @staticmethod
    def _csv_feature_value(field_name: str, value: object) -> object:
        """새 행의 날짜를 기존 학습 CSV와 동일한 형식으로 직렬화한다."""

        if value is None:
            return ""
        if isinstance(value, bool):
            return int(value)
        if field_name == "mac_address" and isinstance(value, str):
            return value.replace("-", ":").lower()
        if (
            field_name in CSV_INTEGER_AMOUNT_COLUMNS
            and isinstance(value, float)
            and value.is_integer()
        ):
            return int(value)
        if field_name in CSV_DURATION_COLUMNS:
            if not isinstance(value, timedelta):
                raise DatasetBuildError(
                    f"확정 라벨 거래의 {field_name} 값이 timedelta가 아닙니다."
                )
            total_seconds = value.total_seconds()
            if total_seconds < 0:
                raise DatasetBuildError(
                    f"확정 라벨 거래의 {field_name} 값이 음수입니다."
                )
            days, remainder = divmod(total_seconds, 24 * 60 * 60)
            hours, remainder = divmod(remainder, 60 * 60)
            minutes, seconds = divmod(remainder, 60)
            return (
                (
                    f"{int(days)} days {int(hours):02d}:{int(minutes):02d}:"
                    f"{seconds:09.6f}"
                )
                .rstrip("0")
                .rstrip(".")
            )
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
            features = assembled.model_dump(mode="python", by_alias=False)
        except ValidationError as exc:
            raise DatasetBuildError(
                "확정 라벨 거래의 원본 Feature가 학습 계약과 맞지 않습니다: "
                f"{transaction.id}"
            ) from exc

        row: dict[str, object] = {name: "" for name in fieldnames}
        for field_name, value in features.items():
            output_name = (
                TRAINING_FLAG_DEPOSIT_ALIAS
                if field_name == TRAINING_FLAG_DEPOSIT_CANONICAL
                else field_name
            )
            row[output_name] = LabeledDatasetBuilder._csv_feature_value(
                field_name,
                value,
            )

        balance_drain_ratio: float | str = ""
        if transaction.initial_balance is not None and transaction.initial_balance > 0:
            balance_drain_ratio = (
                transaction.transaction_amount / transaction.initial_balance
            )
        row.update(
            {
                TRAINING_TRANSACTION_ID_COLUMN: transaction.id,
                TRAINING_IDENTIFICATION_COLUMN: (
                    confirmed.customer.identification_number
                ),
                TRAINING_CUSTOMER_ID_COLUMN: transaction.customer_id,
                TRAINING_BALANCE_DRAIN_RATIO_COLUMN: balance_drain_ratio,
                TRAINING_LABEL_COLUMN: int(confirmed.label.confirmed_is_fraud),
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
            seen_transaction_ids: set[int] = set()

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
                    transaction_id_text = (
                        row.get(TRAINING_TRANSACTION_ID_COLUMN) or ""
                    ).strip()
                    if not transaction_id_text:
                        raise DatasetBuildError(
                            "기존 학습 CSV에 transaction_id가 비어 있는 행이 있습니다."
                        )
                    transaction_id = self._transaction_id(transaction_id_text)
                    if transaction_id in seen_transaction_ids:
                        raise DatasetBuildError(
                            "기존 학습 CSV에 중복 transaction_id가 있습니다: "
                            f"{transaction_id_text}"
                        )
                    seen_transaction_ids.add(transaction_id)
                    source_row_count += 1

                    labeled = confirmed.pop(transaction_id, None)
                    if labeled is not None:
                        row[TRAINING_LABEL_COLUMN] = str(
                            int(labeled.label.confirmed_is_fraud)
                        )
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
                            recipient_account=labeled.recipient_account,
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
    "TRAINING_CSV_COLUMNS",
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
