import unittest
from datetime import UTC, datetime
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
