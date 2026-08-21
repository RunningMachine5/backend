"""운영 시연용 거래 100건을 실제 탐지 Pipeline에 순차 주입한다."""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import sleep

from sqlmodel import Session

from app.core.db import engine
from app.dto.demo_transaction import DemoTransactionInjectionStatus
from app.dto.transaction import TransactionRequestDTO
from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.repositories.derived_features import DerivedFeaturesRepository
from app.repositories.feature_context import FeatureContextRepository
from app.repositories.transaction import TransactionRepository
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
    / "transaction_august_2.csv"
)


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
    return value.lower() in {"true", "1"}


def _csv_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _nullable(value: str) -> str | None:
    return value or None


def load_demo_transaction_rows(
    csv_path: Path = DEMO_TRANSACTION_CSV,
) -> list[TransactionRequestDTO]:
    rows: list[TransactionRequestDTO] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        for source_row in csv.DictReader(source):
            rows.append(
                TransactionRequestDTO(
                    customer_id=int(source_row["customer_id"]),
                    source_account_number=source_row["source_account_number"],
                    recipient_account_number=source_row["recipient_account_number"],
                    transaction_datetime=_csv_datetime(
                        source_row["transaction_datetime"]
                    ),
                    transaction_amount=int(source_row["transaction_amount"]),
                    channel=source_row["channel"],
                    type_general_automatic=source_row["type_general_automatic"],
                    access_medium=_nullable(source_row["access_medium"]),
                    num_connection_failure=int(
                        source_row["num_connection_failure"]
                    ),
                    operating_system=_nullable(source_row["operating_system"]),
                    ip_address=_nullable(source_row["ip_address"]),
                    mac_address=_nullable(source_row["mac_address"]),
                    location_lat=float(source_row["location_lat"]),
                    location_lon=float(source_row["location_lon"]),
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
                        source_row["customer_flag_terminal_malicious_behavior_1"]
                    ),
                    customer_flag_terminal_malicious_behavior_2=_csv_bool(
                        source_row["customer_flag_terminal_malicious_behavior_2"]
                    ),
                    customer_flag_terminal_malicious_behavior_3=_csv_bool(
                        source_row["customer_flag_terminal_malicious_behavior_3"]
                    ),
                    customer_flag_terminal_malicious_behavior_5=_csv_bool(
                        source_row["customer_flag_terminal_malicious_behavior_5"]
                    ),
                    customer_flag_terminal_malicious_behavior_6=_csv_bool(
                        source_row["customer_flag_terminal_malicious_behavior_6"]
                    ),
                )
            )
            if len(rows) == DEMO_TRANSACTION_COUNT:
                break
    if len(rows) < DEMO_TRANSACTION_COUNT:
        raise ValueError("시연 거래 CSV에는 최소 100건이 있어야 합니다.")
    return rows


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
                payload = row.model_copy(
                    update={"transaction_datetime": datetime.now(UTC)}
                )
                result = _pipeline(session, ml_serving_client).run(payload)
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
