import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.demo_transaction import get_demo_transaction_runner, router
from app.services.demo_transaction_injection import (
    DEMO_TRANSACTION_COUNT,
    DemoTransactionInjectionManager,
    load_demo_transaction_rows,
)


class DemoTransactionResourceTest(unittest.TestCase):
    def test_csv_contains_90_normal_and_10_fraud_rows(self) -> None:
        rows = load_demo_transaction_rows()

        self.assertEqual(len(rows), DEMO_TRANSACTION_COUNT)
        self.assertEqual(
            sum(row.confirmed_is_fraud for row in rows),
            10,
        )

    def test_manager_rejects_overlapping_run(self) -> None:
        manager = DemoTransactionInjectionManager()

        self.assertTrue(manager.start())
        self.assertFalse(manager.start())


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
        self.app.dependency_overrides[get_demo_transaction_runner] = (
            lambda: manager.complete
        )

        with patch(
            "app.api.demo_transaction.demo_transaction_injection_manager",
            manager,
        ):
            started = self.client.post(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )
            completed = self.client.get(
                "/demo-transactions/injection",
                headers={"X-MLOps-Admin-Token": "admin-secret"},
            )

        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["state"], "RUNNING")
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["state"], "COMPLETED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_rejects_second_start_while_running(self) -> None:
        manager = DemoTransactionInjectionManager()
        self.app.dependency_overrides[get_demo_transaction_runner] = lambda: lambda: None

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


if __name__ == "__main__":
    unittest.main()
