import unittest
from datetime import UTC, datetime
from threading import Event, Thread
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.demo_transaction import get_demo_transaction_runner, router
from app.services.demo_transaction_injection import (
    DEMO_TRANSACTION_COUNT,
    DemoTransactionInjectionManager,
    load_demo_transaction_rows,
    run_demo_transaction_injection,
)


class DemoTransactionResourceTest(unittest.TestCase):
    def test_csv_supports_each_transaction_count_option(self) -> None:
        for transaction_count in (100, 500, 1000):
            with self.subTest(transaction_count=transaction_count):
                rows = load_demo_transaction_rows(transaction_count)
                self.assertEqual(len(rows), transaction_count)

    def test_csv_preserves_first_transaction_api_row(self) -> None:
        rows = load_demo_transaction_rows()

        self.assertEqual(len(rows), DEMO_TRANSACTION_COUNT)
        self.assertEqual(rows[0].customer_id, 30)
        self.assertEqual(rows[0].source_account_number, "110-664-000030")
        self.assertEqual(
            rows[0].transaction_datetime,
            datetime(2026, 8, 1, 0, 18, 51, tzinfo=UTC),
        )
        self.assertTrue(
            all(
                row.channel in {"mobile", "internet", "atm", "others"}
                for row in rows
            )
        )

    def test_manager_uses_selected_options_and_rejects_overlapping_run(self) -> None:
        manager = DemoTransactionInjectionManager()

        self.assertTrue(manager.start(500, 20))
        status = manager.snapshot()

        self.assertEqual(status.total_count, 500)
        self.assertEqual(status.transactions_per_second, 20)
        self.assertFalse(manager.start())

    def test_publishes_transaction_patch_for_demo_transaction(self) -> None:
        row = MagicMock()
        result = MagicMock()
        result.response.prediction_status = "COMPLETED"
        pipeline = MagicMock()
        pipeline.run.return_value = result
        manager = MagicMock()
        dashboard_event = {
            "source": "demo_transaction",
            "transaction_patch": {"event_id": "transaction:1"},
        }

        with (
            patch(
                "app.services.demo_transaction_injection.load_demo_transaction_rows",
                return_value=[row],
            ),
            patch(
                "app.services.demo_transaction_injection._pipeline",
                return_value=pipeline,
            ),
            patch("app.services.demo_transaction_injection.Session"),
            patch(
                "app.services.demo_transaction_injection.build_agent_input",
                return_value=None,
            ),
            patch(
                "app.services.demo_transaction_injection."
                "build_transaction_dashboard_event",
                return_value=dashboard_event,
            ) as build_event,
            patch(
                "app.services.demo_transaction_injection.dashboard_event_broker.publish"
            ) as publish,
            patch(
                "app.services.demo_transaction_injection."
                "demo_transaction_injection_manager",
                manager,
            ),
        ):
            run_demo_transaction_injection(MagicMock(), transaction_count=1)

        build_event.assert_called_once_with(
            source="demo_transaction",
            result=result,
            agent_input=None,
        )
        publish.assert_called_once_with(
            event="dashboard_updated",
            data=dashboard_event,
        )
        manager.complete.assert_called_once_with()

    def test_agent_analysis_does_not_block_next_demo_transaction(self) -> None:
        rows = [MagicMock(), MagicMock()]
        result = MagicMock()
        result.response.prediction_status = "DECLINED"
        pipeline = MagicMock()
        second_transaction_processed = Event()
        processed_count = 0

        def run_pipeline(_):
            nonlocal processed_count
            processed_count += 1
            if processed_count == 2:
                second_transaction_processed.set()
            return result

        pipeline.run.side_effect = run_pipeline
        agent_input = MagicMock()
        agent_started = Event()
        release_agent = Event()

        def wait_for_release(_):
            agent_started.set()
            release_agent.wait(timeout=2)

        with (
            patch(
                "app.services.demo_transaction_injection.load_demo_transaction_rows",
                return_value=rows,
            ),
            patch(
                "app.services.demo_transaction_injection._pipeline",
                return_value=pipeline,
            ),
            patch("app.services.demo_transaction_injection.Session"),
            patch(
                "app.services.demo_transaction_injection.build_agent_input",
                return_value=agent_input,
            ),
            patch(
                "app.services.demo_transaction_injection."
                "build_transaction_dashboard_event",
                return_value={"source": "demo_transaction"},
            ),
            patch(
                "app.services.demo_transaction_injection.dashboard_event_broker.publish"
            ),
            patch(
                "app.services.demo_transaction_injection."
                "demo_transaction_injection_manager"
            ) as manager,
            patch(
                "app.services.demo_transaction_injection.run_demo_agent_task",
                side_effect=wait_for_release,
            ),
            patch("app.services.demo_transaction_injection.sleep"),
        ):
            injection_thread = Thread(
                target=run_demo_transaction_injection,
                args=(MagicMock(),),
                kwargs={
                    "transaction_count": 2,
                    "transactions_per_second": 20,
                },
            )
            injection_thread.start()
            try:
                self.assertTrue(agent_started.wait(timeout=3))
                self.assertTrue(second_transaction_processed.wait(timeout=3))
                manager.complete.assert_not_called()
            finally:
                release_agent.set()
                injection_thread.join(timeout=3)

        self.assertFalse(injection_thread.is_alive())
        self.assertEqual(manager.record.call_count, 2)
        manager.complete.assert_called_once_with()

    def test_pipeline_failure_cancels_waiting_agent_tasks(self) -> None:
        rows = [MagicMock(), MagicMock()]
        result = MagicMock()
        result.response.prediction_status = "DECLINED"
        pipeline = MagicMock()
        pipeline.run.side_effect = [result, RuntimeError("pipeline")]
        agent_input = MagicMock()

        with (
            patch(
                "app.services.demo_transaction_injection.load_demo_transaction_rows",
                return_value=rows,
            ),
            patch(
                "app.services.demo_transaction_injection._pipeline",
                return_value=pipeline,
            ),
            patch("app.services.demo_transaction_injection.Session"),
            patch(
                "app.services.demo_transaction_injection.build_agent_input",
                return_value=agent_input,
            ),
            patch(
                "app.services.demo_transaction_injection."
                "build_transaction_dashboard_event",
                return_value={"source": "demo_transaction"},
            ),
            patch(
                "app.services.demo_transaction_injection.dashboard_event_broker.publish"
            ),
            patch(
                "app.services.demo_transaction_injection."
                "demo_transaction_injection_manager"
            ) as manager,
            patch(
                "app.services.demo_transaction_injection.ThreadPoolExecutor"
            ) as executor_class,
            patch(
                "app.services.demo_transaction_injection.run_demo_agent_task"
            ) as agent_task,
            patch(
                "app.services.demo_transaction_injection.monotonic",
                side_effect=[10.0, 10.1, 10.2],
            ),
            patch("app.services.demo_transaction_injection.sleep"),
            self.assertLogs(
                "app.services.demo_transaction_injection",
                "ERROR",
            ),
        ):
            run_demo_transaction_injection(
                MagicMock(),
                transaction_count=2,
                transactions_per_second=2,
            )

        executor = executor_class.return_value
        executor.submit.assert_called_once_with(agent_task, agent_input)
        executor.shutdown.assert_called_once_with(
            wait=False,
            cancel_futures=True,
        )
        manager.fail.assert_called_once_with("pipeline")
        manager.complete.assert_not_called()

    def test_pacing_waits_only_for_time_remaining_after_each_transaction(
        self,
    ) -> None:
        rows = [MagicMock(), MagicMock(), MagicMock()]
        result = MagicMock()
        result.response.prediction_status = "COMPLETED"
        pipeline = MagicMock()
        pipeline.run.return_value = result
        manager = MagicMock()

        with (
            patch(
                "app.services.demo_transaction_injection.load_demo_transaction_rows",
                return_value=rows,
            ),
            patch(
                "app.services.demo_transaction_injection._pipeline",
                return_value=pipeline,
            ),
            patch("app.services.demo_transaction_injection.Session"),
            patch(
                "app.services.demo_transaction_injection.build_agent_input",
                return_value=None,
            ),
            patch(
                "app.services.demo_transaction_injection."
                "build_transaction_dashboard_event",
                return_value={"source": "demo_transaction"},
            ),
            patch(
                "app.services.demo_transaction_injection.dashboard_event_broker.publish"
            ),
            patch(
                "app.services.demo_transaction_injection."
                "demo_transaction_injection_manager",
                manager,
            ),
            patch(
                "app.services.demo_transaction_injection.monotonic",
                side_effect=[10.0, 10.2, 10.5, 11.2, 11.2],
            ),
            patch("app.services.demo_transaction_injection.sleep") as sleep_mock,
        ):
            run_demo_transaction_injection(
                MagicMock(),
                transaction_count=3,
                transactions_per_second=2,
            )

        sleep_mock.assert_called_once()
        self.assertAlmostEqual(sleep_mock.call_args.args[0], 0.3)
        self.assertEqual(manager.record.call_count, 3)
        manager.complete.assert_called_once_with()


class DemoTransactionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_starts_background_run_and_returns_completed_status(self) -> None:
        manager = DemoTransactionInjectionManager()
        received_options: dict[str, int] = {}

        def complete(**options: int) -> None:
            received_options.update(options)
            manager.complete()

        self.app.dependency_overrides[get_demo_transaction_runner] = (
            lambda: complete
        )

        with patch(
            "app.api.demo_transaction.demo_transaction_injection_manager",
            manager,
        ):
            started = self.client.post(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
                json={
                    "transaction_count": 500,
                    "transactions_per_second": 20,
                },
            )
            completed = self.client.get(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )

        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["state"], "RUNNING")
        self.assertEqual(started.json()["total_count"], 500)
        self.assertEqual(started.json()["transactions_per_second"], 20)
        self.assertEqual(
            received_options,
            {"transaction_count": 500, "transactions_per_second": 20},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["state"], "COMPLETED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_rejects_second_start_while_running(self) -> None:
        manager = DemoTransactionInjectionManager()
        self.app.dependency_overrides[get_demo_transaction_runner] = (
            lambda: lambda **_: None
        )

        with patch(
            "app.api.demo_transaction.demo_transaction_injection_manager",
            manager,
        ):
            first = self.client.post(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )
            duplicate = self.client.post(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_uses_default_options_without_request_body(self) -> None:
        manager = DemoTransactionInjectionManager()
        received_options: dict[str, int] = {}
        self.app.dependency_overrides[get_demo_transaction_runner] = (
            lambda: lambda **options: received_options.update(options)
        )

        with patch(
            "app.api.demo_transaction.demo_transaction_injection_manager",
            manager,
        ):
            response = self.client.post(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["total_count"], 100)
        self.assertEqual(response.json()["transactions_per_second"], 1)
        self.assertEqual(
            received_options,
            {"transaction_count": 100, "transactions_per_second": 1},
        )


if __name__ == "__main__":
    unittest.main()
