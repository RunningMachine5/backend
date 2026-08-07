import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient

from app.services.mlops.cloud_run import get_cloud_run_admin_client
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data


class MLOpsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = Mock()
        app.dependency_overrides[get_cloud_run_admin_client] = lambda: self.admin
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_run_requires_admin_token(self) -> None:
        response = self.client.post("/mlops/training/runs", json={})

        self.assertEqual(response.status_code, 401)
        self.admin.run_training.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "")
    def test_empty_admin_token_disables_management_api(self) -> None:
        response = self.client.get(
            "/mlops/serving/status",
            headers={"X-MLOps-Admin-Token": "anything"},
        )

        self.assertEqual(response.status_code, 503)
        self.admin.get_serving_status.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_run_returns_operation_id(self) -> None:
        self.admin.run_training.return_value = {
            "name": "projects/test/locations/region/operations/train-op"
        }

        response = self.client.post(
            "/mlops/training/runs",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "auto_promote": True,
                "min_pr_auc": 0.75,
                "min_recall": 0.8,
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation_id"], "train-op")
        self.admin.run_training.assert_called_once_with(
            auto_promote=True,
            min_pr_auc=0.75,
            min_recall=0.8,
            dataset_uri=None,
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_promotion_passes_validated_raw_features_to_smoke_prediction(self) -> None:
        self.admin.promote_model_revision.return_value = {
            "operation": {
                "name": "projects/test/locations/region/operations/promote-op"
            },
            "revision": "serving-00002-new",
            "smokePrediction": {},
        }

        response = self.client.post(
            "/mlops/serving/promotions",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "model_version": "17",
                "transaction_id": "TX-SMOKE",
                "features": valid_ml_raw_data(),
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation_id"], "promote-op")
        call = self.admin.promote_model_revision.call_args.kwargs
        self.assertEqual(call["model_version"], "17")
        self.assertEqual(call["transaction_id"], "TX-SMOKE")
        self.assertEqual(len(call["features"]), 54)
        self.assertIn("Time Difference", call["features"])


if __name__ == "__main__":
    unittest.main()
