"""운영 시연용 거래를 실제 탐지 Pipeline에 순차 주입한다."""

from __future__ import annotations

import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

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
from app.services.dashboard.transaction_patch import (
    build_transaction_dashboard_event,
)
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClient
from app.services.transaction.detection_result_service import DetectionResultService
from app.services.transaction.transaction_service import TransactionService

logger = logging.getLogger(__name__)

DEMO_TRANSACTION_COUNT = 100
DEMO_TRANSACTIONS_PER_SECOND = 1
DEMO_AGENT_WORKER_COUNT = 2
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

    def start(
        self,
        transaction_count: int = DEMO_TRANSACTION_COUNT,
        transactions_per_second: int = DEMO_TRANSACTIONS_PER_SECOND,
    ) -> bool:
        with self._lock:
            if self._status.state == "RUNNING":
                return False
            self._status = DemoTransactionInjectionStatus(
                state="RUNNING",
                total_count=transaction_count,
                transactions_per_second=transactions_per_second,
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
    transaction_count: int = DEMO_TRANSACTION_COUNT,
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
            if len(rows) == transaction_count:
                break
    if len(rows) < transaction_count:
        raise ValueError(
            f"시연 거래 CSV에는 최소 {transaction_count}건이 있어야 합니다."
        )
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
    transaction_count: int = DEMO_TRANSACTION_COUNT,
    transactions_per_second: int = DEMO_TRANSACTIONS_PER_SECOND,
) -> None:
    """선택한 거래를 지정한 최대 속도로 처리하고 진행 상태를 갱신한다."""

    agent_executor: ThreadPoolExecutor | None = None
    try:
        rows = load_demo_transaction_rows(transaction_count)
        interval_seconds = 1 / transactions_per_second
        # Agent 분석은 수 초 걸릴 수 있으므로 거래 주입과 분리한다.
        # Worker 수를 제한해 DB 연결과 외부 AI 요청이 한꺼번에 몰리지 않게 한다.
        agent_executor = ThreadPoolExecutor(
            max_workers=DEMO_AGENT_WORKER_COUNT,
        )
        for index, row in enumerate(rows):
            transaction_started_at = monotonic()
            with Session(engine) as session:
                payload = row.model_copy(
                    update={"transaction_datetime": datetime.now(UTC)}
                )
                result = _pipeline(session, ml_serving_client).run(payload)
                session.commit()
                agent_input = build_agent_input(result)
                dashboard_event = build_transaction_dashboard_event(
                    source="demo_transaction",
                    result=result,
                    agent_input=agent_input,
                )

            dashboard_event_broker.publish(
                event="dashboard_updated",
                data=dashboard_event,
            )
            if agent_input is not None:
                agent_executor.submit(run_demo_agent_task, agent_input)
            demo_transaction_injection_manager.record(
                result.response.prediction_status
            )
            if index < len(rows) - 1:
                remaining_seconds = interval_seconds - (
                    monotonic() - transaction_started_at
                )
                if remaining_seconds > 0:
                    sleep(remaining_seconds)
    except Exception as exc:
        if agent_executor is not None:
            agent_executor.shutdown(wait=False, cancel_futures=True)
        logger.exception("시연 거래 주입 실패")
        demo_transaction_injection_manager.fail(str(exc))
    else:
        # 새 시연과 이전 Agent 분석이 겹치지 않도록 모두 끝난 뒤 완료 처리한다.
        agent_executor.shutdown(wait=True)
        demo_transaction_injection_manager.complete()


__all__ = [
    "DEMO_TRANSACTION_COUNT",
    "DemoTransactionInjectionManager",
    "demo_transaction_injection_manager",
    "load_demo_transaction_rows",
    "run_demo_transaction_injection",
]
