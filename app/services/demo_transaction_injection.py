"""운영 시연용 거래 100건을 실제 탐지 Pipeline에 순차 주입한다."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from time import sleep

from sqlmodel import Session, select

from app.core.db import engine
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.dto.demo_transaction import DemoTransactionInjectionStatus
from app.dto.transaction import TransactionRequestDTO
from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.repositories.derived_features import DerivedFeaturesRepository
from app.repositories.feature_context import FeatureContextRepository
from app.repositories.transaction import (
    TransactionLabelRepository,
    TransactionRepository,
)
from app.services.agent.input_builder import build_agent_input
from app.services.agent.task_runner import run_demo_agent_task
from app.services.dashboard.dashboard_event_broker import dashboard_event_broker
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClient
from app.services.transaction.detection_result_service import DetectionResultService
from app.services.transaction.transaction_service import TransactionService

logger = logging.getLogger(__name__)

DEMO_TRANSACTION_COUNT = 100
DEMO_TRANSACTION_INTERVAL_SECONDS = 1.0
DEMO_TRANSACTION_CSV = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "demo"
    / "transactions_100.csv"
)


@dataclass(frozen=True, slots=True)
class DemoTransactionRow:
    source_id: str
    customer_name: str
    customer_birth_date: date
    customer_gender: str
    customer_identification_number: str
    customer_registration_datetime: datetime
    customer_credit_rating: int
    customer_loan_type: str
    source_account_number: str
    source_account_type: str
    source_account_creation_datetime: datetime
    source_account_balance: int
    source_account_daily_limit: int
    source_account_openbanking: bool
    recipient_account_number: str
    recipient_account_suspended: bool
    payload: TransactionRequestDTO
    confirmed_is_fraud: bool


class DemoTransactionInjectionManager:
    """한 Backend 프로세스에서 시연 주입 한 건만 실행되도록 상태를 관리한다."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._status = DemoTransactionInjectionStatus(state="IDLE")

    def start(self) -> bool:
        with self._lock:
            if self._status.state == "RUNNING":
                return False
            self._status = DemoTransactionInjectionStatus(
                state="RUNNING",
                total_count=DEMO_TRANSACTION_COUNT,
                started_at=datetime.now(UTC),
            )
            return True

    def record(self, prediction_status: str) -> None:
        with self._lock:
            self._status.processed_count += 1
            if prediction_status == "DECLINED":
                self._status.declined_count += 1
            else:
                self._status.approved_count += 1

    def complete(self) -> None:
        with self._lock:
            self._status.state = "COMPLETED"
            self._status.finished_at = datetime.now(UTC)

    def fail(self, message: str) -> None:
        with self._lock:
            self._status.state = "FAILED"
            self._status.finished_at = datetime.now(UTC)
            self._status.error_message = message

    def snapshot(self) -> DemoTransactionInjectionStatus:
        with self._lock:
            return self._status.model_copy(deep=True)


demo_transaction_injection_manager = DemoTransactionInjectionManager()


def _csv_bool(value: str) -> bool:
    return value == "1"


def _csv_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _nullable(value: str) -> str | None:
    return value or None


def _location(value: str) -> tuple[float, float]:
    match = re.search(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)$", value)
    if match is None:
        raise ValueError("시연 CSV의 위치 좌표를 읽을 수 없습니다.")
    return float(match.group(1)), float(match.group(2))


def load_demo_transaction_rows(
    csv_path: Path = DEMO_TRANSACTION_CSV,
) -> list[DemoTransactionRow]:
    rows: list[DemoTransactionRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        for source_row in csv.DictReader(source):
            latitude, longitude = _location(source_row["location"])
            rows.append(
                DemoTransactionRow(
                    source_id=source_row["transaction_id"],
                    customer_name=source_row["customer_name"],
                    customer_birth_date=date.fromisoformat(
                        source_row["customer_birth_date"]
                    ),
                    customer_gender=source_row["customer_gender"],
                    customer_identification_number=source_row[
                        "customer_identification_number"
                    ],
                    customer_registration_datetime=_csv_datetime(
                        source_row["customer_registration_datetime"]
                    ),
                    customer_credit_rating=int(source_row["customer_credit_rating"]),
                    customer_loan_type=source_row["customer_loan_type"],
                    source_account_number=source_row["source_account_number"],
                    source_account_type=source_row["account_account_type"],
                    source_account_creation_datetime=_csv_datetime(
                        source_row["account_creation_datetime"]
                    ),
                    source_account_balance=int(source_row["account_initial_balance"]),
                    source_account_daily_limit=int(
                        source_row["account_amount_daily_limit"]
                    ),
                    source_account_openbanking=_csv_bool(
                        source_row["account_indicator_openbanking"]
                    ),
                    recipient_account_number=source_row["recipient_account_number"],
                    recipient_account_suspended=_csv_bool(
                        source_row["recipient_account_suspend_status"]
                    ),
                    payload=TransactionRequestDTO(
                        customer_id=None,
                        source_account_number=source_row["source_account_number"],
                        recipient_account_number=source_row[
                            "recipient_account_number"
                        ],
                        transaction_datetime=datetime.now(UTC),
                        transaction_amount=int(source_row["transaction_amount"]),
                        channel=source_row["channel"],
                        type_general_automatic=source_row["type_general_automatic"],
                        access_medium=_nullable(source_row["access_medium"]),
                        num_connection_failure=int(
                            source_row["transaction_num_connection_failure"]
                        ),
                        operating_system=_nullable(source_row["operating_system"]),
                        ip_address=_nullable(source_row["ip_address"]),
                        mac_address=_nullable(source_row["mac_address"]),
                        location_lat=latitude,
                        location_lon=longitude,
                        customer_rooting_jailbreak_indicator=_csv_bool(
                            source_row["customer_rooting_jailbreak_indicator"]
                        ),
                        customer_mobile_roaming_indicator=_csv_bool(
                            source_row["customer_mobile_roaming_indicator"]
                        ),
                        customer_vpn_indicator=_csv_bool(
                            source_row["customer_vpn_indicator"]
                        ),
                        customer_flag_terminal_malicious_behavior_1=_csv_bool(
                            source_row[
                                "customer_flag_terminal_malicious_behavior_1"
                            ]
                        ),
                        customer_flag_terminal_malicious_behavior_2=_csv_bool(
                            source_row[
                                "customer_flag_terminal_malicious_behavior_2"
                            ]
                        ),
                        customer_flag_terminal_malicious_behavior_3=_csv_bool(
                            source_row[
                                "customer_flag_terminal_malicious_behavior_3"
                            ]
                        ),
                        customer_flag_terminal_malicious_behavior_5=_csv_bool(
                            source_row[
                                "customer_flag_terminal_malicious_behavior_5"
                            ]
                        ),
                        customer_flag_terminal_malicious_behavior_6=_csv_bool(
                            source_row[
                                "customer_flag_terminal_malicious_behavior_6"
                            ]
                        ),
                    ),
                    confirmed_is_fraud=_csv_bool(source_row["is_fraud"]),
                )
            )
    if len(rows) != DEMO_TRANSACTION_COUNT:
        raise ValueError("시연 거래 CSV는 정확히 100건이어야 합니다.")
    return rows


def _ensure_demo_context(session: Session, row: DemoTransactionRow) -> int:
    source_account = session.exec(
        select(Account).where(
            Account.account_number == row.source_account_number
        )
    ).one_or_none()
    if source_account is None:
        customer = session.exec(
            select(Customer).where(
                Customer.identification_number
                == row.customer_identification_number
            )
        ).one_or_none()
        if customer is None:
            customer = Customer(
                name=row.customer_name,
                birth_date=row.customer_birth_date,
                gender=row.customer_gender,
                identification_number=row.customer_identification_number,
                registration_datetime=row.customer_registration_datetime,
                credit_rating=row.customer_credit_rating,
                loan_type=row.customer_loan_type,
            )
            session.add(customer)
            session.flush()
        source_account = Account(
            customer_id=customer.id,
            account_number=row.source_account_number,
            account_type=row.source_account_type,
            creation_datetime=row.source_account_creation_datetime,
            current_balance=row.source_account_balance,
            amount_daily_limit=row.source_account_daily_limit,
            indicator_openbanking=row.source_account_openbanking,
        )
        session.add(source_account)

    recipient_account = session.exec(
        select(Account).where(
            Account.account_number == row.recipient_account_number
        )
    ).one_or_none()
    if recipient_account is None:
        session.add(
            Account(
                account_number=row.recipient_account_number,
                suspend_status=row.recipient_account_suspended,
            )
        )
    session.commit()
    assert source_account.customer_id is not None
    return source_account.customer_id


def _pipeline(session: Session, ml_serving_client: MLServingClient) -> DFraudDetectionPipeline:
    return DFraudDetectionPipeline(
        derived_features_service=DerivedFeatureService(
            FeatureContextRepository(session),
            DerivedFeaturesRepository(session),
        ),
        ml_serving_client=ml_serving_client,
        transaction_service=TransactionService(TransactionRepository(session)),
        detection_result_service=DetectionResultService(session),
    )


def run_demo_transaction_injection(
    ml_serving_client: MLServingClient,
    *,
    interval_seconds: float = DEMO_TRANSACTION_INTERVAL_SECONDS,
) -> None:
    """100건을 초당 최대 1건씩 처리하고 진행 상태를 갱신한다."""

    try:
        rows = load_demo_transaction_rows()
        for index, row in enumerate(rows):
            with Session(engine) as session:
                customer_id = _ensure_demo_context(session, row)
                payload = row.payload.model_copy(
                    update={
                        "customer_id": customer_id,
                        "transaction_datetime": datetime.now(UTC),
                    }
                )
                result = _pipeline(session, ml_serving_client).run(payload)
                TransactionLabelRepository(session).upsert(
                    transaction_id=result.transaction.id,
                    confirmed_is_fraud=row.confirmed_is_fraud,
                )
                session.commit()
                agent_input = build_agent_input(result)

            dashboard_event_broker.publish(
                event="dashboard_updated",
                data={"source": "demo_transaction"},
            )
            if agent_input is not None:
                run_demo_agent_task(agent_input)
            demo_transaction_injection_manager.record(
                result.response.prediction_status
            )
            if index < len(rows) - 1:
                sleep(interval_seconds)
        demo_transaction_injection_manager.complete()
    except Exception as exc:
        logger.exception("시연 거래 주입 실패")
        demo_transaction_injection_manager.fail(str(exc))


__all__ = [
    "DEMO_TRANSACTION_COUNT",
    "DemoTransactionInjectionManager",
    "demo_transaction_injection_manager",
    "load_demo_transaction_rows",
    "run_demo_transaction_injection",
]
