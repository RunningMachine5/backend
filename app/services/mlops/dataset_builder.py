"""확정 라벨을 기존 원본 CSV에 반영해 새 학습 데이터셋을 만든다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
from sqlmodel import Session, func, select

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_features import (
    RAW_TRANSACTION_FEATURE_COLUMNS,
    MLTransactionFeatures,
)
from app.services.features.ml_feature_assembler import assemble_ml_features

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CSV_DATE_FORMAT = "%Y-%m-%d"
CSV_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S%z"
CSV_DATETIME_COLUMNS = frozenset(
    {
        "customer_birth_date",
        "customer_registration_datetime",
        "account_creation_datetime",
        "transaction_datetime",
        "last_atm_transaction_datetime",
        "last_bank_branch_transaction_datetime",
        "recipient_transaction_resumed_date",
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
TRAINING_LABEL_COLUMN = "is_fraud"

# ML 최신 학습 계약은 raw51에 거래 ID와 정답 라벨을 붙인 53열이다.
TRAINING_CSV_COLUMNS = (
    TRAINING_TRANSACTION_ID_COLUMN,
    *RAW_TRANSACTION_FEATURE_COLUMNS,
    TRAINING_LABEL_COLUMN,
)
MLOPS_BASE_DATASET_URI = (
    "gs://fdshield-ml-data-801817539291/base/train1.csv"
)
MLOPS_BASE_DATASET_PERIOD_START = date(2026, 1, 1)
MLOPS_BASE_DATASET_PERIOD_END = date(2026, 7, 31)
if len(TRAINING_CSV_COLUMNS) != 53:  # pragma: no cover - import invariant
    raise RuntimeError("TRAINING_CSV_COLUMNS must contain exactly 53 columns.")
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

    def delete(self, uri: str) -> None: ...


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

    def delete(self, uri: str) -> None:
        target = parse_gcs_uri(uri)
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{quote(target.bucket, safe='')}/o/{quote(target.name, safe='')}"
        )
        response = None
        try:
            response = self._session.delete(url, timeout=(10, 60))
            if response.status_code == 404:
                return
            response.raise_for_status()
        except Exception as exc:
            raise DatasetStorageError(
                f"학습 데이터셋을 GCS에서 삭제하지 못했습니다: {uri}"
            ) from exc
        finally:
            if response is not None:
                response.close()


@dataclass(frozen=True)
class DatasetBuildResult:
    source_row_count: int
    output_row_count: int
    confirmed_label_count: int
    appended_label_count: int
    normal_count: int
    fraud_count: int


@dataclass(frozen=True)
class DatasetLabelSummary:
    normal_count: int
    fraud_count: int

    @property
    def labeled_count(self) -> int:
        return self.normal_count + self.fraud_count


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
    """고정 train1 CSV에 확정 라벨 거래를 추가해 새 버전을 만든다.

    기존 GCS 객체는 수정하지 않는다. 원본 행은 그대로 복사하고 DB의 정규화
    테이블에서 확정 라벨 거래를 ML 학습용 53열 행으로 복원해 추가한다.
    """

    def __init__(
        self,
        storage: ObjectStorage,
        *,
        source_uri: str = MLOPS_BASE_DATASET_URI,
    ) -> None:
        self._storage = storage
        self._source_uri = source_uri

    @staticmethod
    def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
        """선택한 날짜 전체를 UTC 거래 시각 범위로 바꾼다."""

        if period_start > period_end:
            raise DatasetBuildError("기간 시작일은 종료일보다 늦을 수 없습니다.")
        if period_start <= MLOPS_BASE_DATASET_PERIOD_END:
            raise DatasetBuildError(
                "추가 기간은 기본 데이터 다음 날인 2026-08-01부터 선택할 수 있습니다."
            )
        return (
            datetime.combine(period_start, time.min, tzinfo=UTC),
            datetime.combine(period_end, time.max, tzinfo=UTC),
        )

    @classmethod
    def label_summary(
        cls,
        session: Session,
        *,
        period_start: date,
        period_end: date,
    ) -> DatasetLabelSummary:
        """선택 기간에 담당자가 확정한 정상·사기 건수를 센다."""

        start_at, end_at = cls._period_bounds(period_start, period_end)
        rows = session.exec(
            select(TransactionLabel.confirmed_is_fraud, func.count())
            .join(Transaction, Transaction.id == TransactionLabel.transaction_id)
            .where(
                Transaction.transaction_datetime >= start_at,
                Transaction.transaction_datetime <= end_at,
            )
            .group_by(TransactionLabel.confirmed_is_fraud)
        ).all()
        counts = {bool(is_fraud): count for is_fraud, count in rows}
        return DatasetLabelSummary(
            normal_count=counts.get(False, 0),
            fraud_count=counts.get(True, 0),
        )

    @classmethod
    def _confirmed_transactions(
        cls,
        session: Session,
        *,
        period_start: date,
        period_end: date,
    ) -> dict[int, ConfirmedTransaction]:
        start_at, end_at = cls._period_bounds(period_start, period_end)
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
            .where(
                Transaction.transaction_datetime >= start_at,
                Transaction.transaction_datetime <= end_at,
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
    def _validate_header(fieldnames: list[str] | None) -> None:
        if not fieldnames:
            raise DatasetBuildError("기존 학습 CSV에 헤더가 없습니다.")
        provided = tuple(fieldnames)
        duplicates = sorted(
            {column for column in provided if provided.count(column) > 1}
        )
        actual = set(provided)
        if not duplicates and actual == set(TRAINING_CSV_COLUMNS):
            return

        raise DatasetBuildError(
            "기존 학습 CSV는 ML 학습용 53열 헤더여야 합니다: "
            f"columns={len(provided)}, duplicates={duplicates}, "
            "supported_columns=False"
        )

    @staticmethod
    def _normalize_source_row(row: dict[str, str]) -> dict[str, object]:
        """입력 순서와 관계없이 ML 학습용 53열 순서로 정렬한다."""

        return {column: row.get(column, "") for column in TRAINING_CSV_COLUMNS}

    @staticmethod
    def _csv_feature_value(field_name: str, value: object) -> object:
        """새 행의 피처를 기존 학습 CSV와 동일한 형식으로 직렬화한다."""

        if value is None:
            return ""
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
        if field_name == "customer_birth_date" and isinstance(value, date):
            return value.strftime(CSV_DATE_FORMAT)
        if not isinstance(value, datetime):
            raise DatasetBuildError(
                f"확정 라벨 거래의 {field_name} 값이 datetime이 아닙니다."
            )
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.strftime(CSV_DATETIME_FORMAT)

    @staticmethod
    def _new_row(
        confirmed: ConfirmedTransaction,
        assembled: MLTransactionFeatures,
    ) -> dict[str, object]:
        transaction = confirmed.transaction
        features = assembled.model_dump(mode="python", by_alias=False)

        row: dict[str, object] = {}
        for field_name, value in features.items():
            row[field_name] = LabeledDatasetBuilder._csv_feature_value(
                field_name,
                value,
            )

        row.update(
            {
                TRAINING_TRANSACTION_ID_COLUMN: transaction.id,
                TRAINING_LABEL_COLUMN: int(confirmed.label.confirmed_is_fraud),
            }
        )
        return row

    def build(
        self,
        session: Session,
        *,
        destination_uri: str,
        period_start: date,
        period_end: date,
    ) -> DatasetBuildResult:
        if parse_gcs_uri(self._source_uri) == parse_gcs_uri(destination_uri):
            raise DatasetBuildError("새 데이터셋은 기존 GCS 객체와 달라야 합니다.")

        confirmed = self._confirmed_transactions(
            session,
            period_start=period_start,
            period_end=period_end,
        )
        if not confirmed:
            raise DatasetBuildError("반영할 확정 거래 라벨이 없습니다.")

        with TemporaryDirectory(prefix="fdshield-dataset-") as temp_directory:
            source_path = Path(temp_directory) / "source.csv"
            output_path = Path(temp_directory) / "output.csv"
            self._storage.download(self._source_uri, source_path)

            source_row_count = 0

            with (
                source_path.open("r", encoding="utf-8-sig", newline="") as source_file,
                output_path.open("w", encoding="utf-8", newline="") as output_file,
            ):
                reader = csv.DictReader(source_file)
                self._validate_header(reader.fieldnames)
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=TRAINING_CSV_COLUMNS,
                    extrasaction="ignore",
                )
                writer.writeheader()

                for row in reader:
                    source_row_count += 1
                    writer.writerow(self._normalize_source_row(row))

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
                    except ValidationError as exc:
                        raise DatasetBuildError(
                            "확정 라벨 거래의 원본 Feature가 학습 계약과 맞지 "
                            f"않습니다: {transaction_id}"
                        ) from exc
                    writer.writerow(
                        self._new_row(
                            labeled,
                            assembled,
                        )
                    )

            appended_label_count = len(confirmed)
            output_row_count = source_row_count + appended_label_count
            self._storage.upload_new(output_path, destination_uri)

        fraud_count = sum(
            1 for item in confirmed.values() if item.label.confirmed_is_fraud
        )
        return DatasetBuildResult(
            source_row_count=source_row_count,
            output_row_count=output_row_count,
            confirmed_label_count=len(confirmed),
            appended_label_count=appended_label_count,
            normal_count=len(confirmed) - fraud_count,
            fraud_count=fraud_count,
        )

    def delete_dataset(self, uri: str) -> None:
        """생성된 데이터셋 객체를 삭제하되 고정 원본은 유지한다."""

        if parse_gcs_uri(uri) == parse_gcs_uri(self._source_uri):
            raise DatasetBuildError("기본 학습 데이터셋은 삭제할 수 없습니다.")
        self._storage.delete(uri)


@lru_cache
def get_labeled_dataset_builder() -> LabeledDatasetBuilder:
    return LabeledDatasetBuilder(GCSObjectStorage())


LabeledDatasetBuilderDep = Annotated[
    LabeledDatasetBuilder,
    Depends(get_labeled_dataset_builder),
]


__all__ = [
    "MLOPS_BASE_DATASET_URI",
    "MLOPS_BASE_DATASET_PERIOD_END",
    "MLOPS_BASE_DATASET_PERIOD_START",
    "TRAINING_CSV_COLUMNS",
    "ConfirmedTransaction",
    "DatasetBuildError",
    "DatasetBuildResult",
    "DatasetLabelSummary",
    "DatasetStorageError",
    "GCSObjectStorage",
    "LabeledDatasetBuilder",
    "LabeledDatasetBuilderDep",
    "get_labeled_dataset_builder",
    "parse_gcs_uri",
]
