"""확정 라벨을 기존 원본 CSV에 반영해 새 학습 데이터셋을 만든다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
from app.dto.ml_features import MLTransactionFeatures
from app.services.features.ml_feature_assembler import assemble_ml_features

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

# 실시간 추론은 담당자의 raw51을 사용하지만 기존 train1.csv는 64열 원본이다.
# 재학습 데이터는 기존 파일에 행을 추가하므로 이 헤더 순서를 그대로 유지한다.
TRAINING_MODEL_INPUT_COLUMNS = (
    "customer_birth_date",
    "customer_gender",
    "customer_name",
    "customer_registration_datetime",
    "customer_credit_rating",
    "customer_flag_change_of_authentication_1",
    "customer_flag_change_of_authentication_2",
    "customer_flag_change_of_authentication_3",
    "customer_flag_change_of_authentication_4",
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_loan_type",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
    "customer_inquery_atm_limit",
    "customer_increase_atm_limit",
    "account_account_number",
    "account_account_type",
    "account_creation_datetime",
    "account_initial_balance",
    "account_balance",
    "account_indicator_release_limit_excess",
    "account_amount_daily_limit",
    "account_indicator_openbanking",
    "account_remaining_amount_daily_limit_exceeded",
    "account_release_suspention",
    "account_one_month_max_amount",
    "account_one_month_std_dev",
    "account_dawn_one_month_max_amount",
    "account_dawn_one_month_std_dev",
    "transaction_datetime",
    "transaction_amount",
    "channel",
    "operating_system",
    "error_code",
    "type_general_automatic",
    "ip_address",
    "mac_address",
    "access_medium",
    "location",
    "recipient_account_number",
    "transaction_num_connection_failure",
    "another_person_account",
    "distance",
    "time_difference",
    "unused_terminal_status",
    "last_atm_transaction_datetime",
    "last_bank_branch_transaction_datetime",
    "flag_deposit_more_than_ten_million",
    "unused_account_status",
    "recipient_account_suspend_status",
    "number_of_transaction_with_the_account",
    "transaction_history_with_the_account",
    "first_time_ios_by_vulnerable_user",
    "transaction_resumed_date",
)
TRAINING_CSV_COLUMNS = (
    TRAINING_TRANSACTION_ID_COLUMN,
    *TRAINING_MODEL_INPUT_COLUMNS[:3],
    TRAINING_IDENTIFICATION_COLUMN,
    *(
        TRAINING_FLAG_DEPOSIT_ALIAS
        if column == TRAINING_FLAG_DEPOSIT_CANONICAL
        else column
        for column in TRAINING_MODEL_INPUT_COLUMNS[3:]
    ),
    TRAINING_CUSTOMER_ID_COLUMN,
    TRAINING_BALANCE_DRAIN_RATIO_COLUMN,
    TRAINING_LABEL_COLUMN,
)
MLOPS_BASE_DATASET_URI = (
    "gs://fdshield-ml-data-801817539291/base/train1.csv"
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
    """고정 train1 CSV에 확정 라벨 거래를 추가해 새 버전을 만든다.

    기존 GCS 객체는 수정하지 않는다. 원본 행은 그대로 복사하고 DB의 정규화
    테이블에서 확정 라벨 거래를 raw64 행으로 복원해 모두 추가한다.
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
        if field_name == "customer_birth_date" and isinstance(value, date):
            return value.strftime("%Y-%m-%d 00:00:00")
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
        features = assembled.model_dump(mode="python", by_alias=False)

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

        # train1.csv에는 남아 있지만 raw51에서는 모델 입력에서 빠진 원본 컬럼이다.
        # 저장된 정규화 값으로 채우고, 더 이상 계산하지 않는 iOS 파생값만 0으로 둔다.
        row.update(
            {
                "customer_name": confirmed.customer.name,
                "account_account_number": confirmed.source_account.account_number,
                "account_release_suspention": int(
                    assembled.recipient_release_suspension
                ),
                "error_code": transaction.error_code or "",
                "ip_address": LabeledDatasetBuilder._csv_feature_value(
                    "ip_address",
                    transaction.ip_address,
                ),
                "mac_address": LabeledDatasetBuilder._csv_feature_value(
                    "mac_address",
                    transaction.mac_address,
                ),
                "location": (
                    f"{transaction.location_lat} {transaction.location_lon}"
                    if transaction.location_lat is not None
                    and transaction.location_lon is not None
                    else ""
                ),
                "recipient_account_number": transaction.recipient_account_number,
                "first_time_ios_by_vulnerable_user": 0,
                "transaction_resumed_date": (
                    LabeledDatasetBuilder._csv_feature_value(
                        "transaction_resumed_date",
                        assembled.recipient_transaction_resumed_date,
                    )
                ),
            }
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
        destination_uri: str,
    ) -> DatasetBuildResult:
        if parse_gcs_uri(self._source_uri) == parse_gcs_uri(destination_uri):
            raise DatasetBuildError("새 데이터셋은 기존 GCS 객체와 달라야 합니다.")

        confirmed = self._confirmed_transactions(session)
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
                fieldnames = self._validate_header(reader.fieldnames)
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=fieldnames,
                    extrasaction="ignore",
                )
                writer.writeheader()

                for row in reader:
                    source_row_count += 1
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
                    except ValidationError as exc:
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
            confirmed_label_count=appended_label_count,
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
    "MLOPS_BASE_DATASET_URI",
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
