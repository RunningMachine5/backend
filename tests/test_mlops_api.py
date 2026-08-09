import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.services.mlops.cloud_run import get_cloud_run_admin_client
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data


class MLOpsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        DatasetVersion.__table__.create(self.engine)
        TrainingRun.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        self.admin = Mock()
        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_cloud_run_admin_client] = lambda: self.admin
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_dataset(
        self,
        *,
        version: str = "generated-v1",
        gcs_uri: str = "gs://bucket/generated/v1/transactions.csv",
        split_datetime: str | None = None,
    ) -> int:
        response = self.client.post(
            "/mlops/datasets",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "version": version,
                "gcs_uri": gcs_uri,
                "row_count": 200000,
                "split_datetime": split_datetime,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

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

        dataset_id = self.create_dataset()
        response = self.client.post(
            "/mlops/training/runs",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "dataset_version_id": dataset_id,
                "min_pr_auc": 0.75,
                "min_recall": 0.8,
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation_id"], "train-op")
        self.admin.run_training.assert_called_once_with(
            auto_promote=False,
            min_pr_auc=0.75,
            min_recall=0.8,
            dataset_uri="gs://bucket/generated/v1/transactions.csv",
            split_datetime=None,
            training_run_id=1,
            champion_model_version=None,
        )
        with Session(self.engine) as session:
            run = session.exec(select(TrainingRun)).one()
            self.assertEqual(run.status, "RUNNING")
            self.assertIn("train-op", run.cloud_run_operation_name)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_run_passes_versioned_raw_dataset_contract(self) -> None:
        self.admin.run_training.return_value = {
            "name": "projects/test/locations/region/operations/train-op"
        }

        dataset_id = self.create_dataset(
            version="synthetic-v1",
            gcs_uri="gs://bucket/synthetic/v1/transactions.csv",
            split_datetime="2026-04-01T00:00:00",
        )
        response = self.client.post(
            "/mlops/training/runs",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "dataset_version_id": dataset_id,
            },
        )

        self.assertEqual(response.status_code, 202)
        self.admin.run_training.assert_called_once_with(
            auto_promote=False,
            min_pr_auc=0.0,
            min_recall=0.0,
            dataset_uri="gs://bucket/synthetic/v1/transactions.csv",
            split_datetime="2026-04-01 00:00:00",
            training_run_id=1,
            champion_model_version=None,
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_run_accepts_raw_transactions_without_companion(
        self,
    ) -> None:
        self.admin.run_training.return_value = {
            "name": "projects/test/locations/region/operations/train-op"
        }
        dataset_id = self.create_dataset()
        response = self.client.post(
            "/mlops/training/runs",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 202)
        self.admin.run_training.assert_called_once_with(
            auto_promote=False,
            min_pr_auc=0.0,
            min_recall=0.0,
            dataset_uri="gs://bucket/generated/v1/transactions.csv",
            split_datetime=None,
            training_run_id=1,
            champion_model_version=None,
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_registration_rejects_non_gcs_contract(self) -> None:
        response = self.client.post(
            "/mlops/datasets",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "version": "invalid-v1",
                "gcs_uri": "https://example.com/transactions.csv",
                "row_count": 10,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.admin.run_training.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_registration_rejects_legacy_companion_uri(self) -> None:
        response = self.client.post(
            "/mlops/datasets",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "version": "generated-v1",
                "gcs_uri": "gs://bucket/transactions.csv",
                "transactions_uri": "gs://bucket/legacy.csv",
                "row_count": 10,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.admin.run_training.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_registration_rejects_timezone_aware_split_datetime(self) -> None:
        response = self.client.post(
            "/mlops/datasets",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "version": "generated-v1",
                "gcs_uri": "gs://bucket/transactions.csv",
                "row_count": 10,
                "split_datetime": "2026-04-01T00:00:00+09:00",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.admin.run_training.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_candidate_result_requires_admin_approval_before_staging(self) -> None:
        self.admin.run_training.return_value = {
            "name": "projects/test/locations/region/operations/train-op"
        }
        self.admin.create_model_revision.return_value = {
            "operation": {
                "name": "projects/test/locations/region/operations/deploy-op"
            },
            "tag": "model-v2",
            "previousTraffic": [],
        }
        dataset_id = self.create_dataset()
        started = self.client.post(
            "/mlops/training/runs",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={"dataset_version_id": dataset_id},
        )
        run_id = started.json()["training_run"]["id"]

        recorded = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "status": "SUCCEEDED",
                "mlflow_run_id": "mlflow-run-2",
                "model_version": "2",
                "comparison_result": {
                    "candidate": {
                        "model_version": "2",
                        "metrics": {
                            "validation_pr_auc": 0.95,
                            "validation_recall": 0.93,
                            "validation_fpr": 0.004,
                        },
                    },
                    "production": {
                        "model_version": "1",
                        "metrics": {
                            "validation_pr_auc": 0.94,
                            "validation_recall": 0.92,
                            "validation_fpr": 0.005,
                        },
                    },
                    "recommendation": "RECOMMENDED",
                },
            },
        )
        self.assertEqual(recorded.status_code, 200)
        self.assertEqual(recorded.json()["status"], "CANDIDATE")
        self.assertEqual(
            recorded.json()["comparison_result"]["production"]["model_version"],
            "1",
        )

        staged = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={"decision": "APPROVE", "reason": "동일 평가셋에서 개선"},
        )
        self.assertEqual(staged.status_code, 202)
        self.assertEqual(staged.json()["training_run"]["status"], "STAGED")
        self.assertIsNone(staged.json()["training_run"]["serving_revision"])
        self.assertEqual(staged.json()["operation_id"], "deploy-op")
        self.admin.create_model_revision.assert_called_once_with("2")

        self.admin.promote_model_revision.return_value = {
            "operation": {
                "name": "projects/test/locations/region/operations/promote-op"
            },
            "revision": "serving-00002-v2",
            "smokePrediction": {},
        }
        promoted = self.client.post(
            "/mlops/serving/promotions",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={
                "training_run_id": run_id,
                "model_version": "2",
                "transaction_id": "TX-SMOKE-V2",
                "features": valid_ml_raw_data(),
            },
        )
        self.assertEqual(promoted.status_code, 202)
        self.admin.get_operation.return_value = {
            "name": "projects/test/locations/region/operations/promote-op",
            "done": True,
            "response": {},
        }
        promoting_detail = self.client.get(
            f"/mlops/training/runs/{run_id}",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
        )
        self.assertEqual(promoting_detail.json()["status"], "PROMOTING")

        completed = self.client.post(
            f"/mlops/training/runs/{run_id}/deployment/complete",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
        )
        self.assertEqual(completed.status_code, 200)
        detail = self.client.get(
            f"/mlops/training/runs/{run_id}",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
        )
        self.assertEqual(detail.json()["status"], "PRODUCTION")
        self.assertEqual(detail.json()["serving_revision"], "serving-00002-v2")
        self.admin.get_operation.assert_called_once_with("promote-op")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_rejecting_candidate_does_not_create_serving_revision(self) -> None:
        dataset = DatasetVersion(
            version="generated-v1",
            gcs_uri="gs://bucket/generated/v1/transactions.csv",
            row_count=100,
        )
        with Session(self.engine) as session:
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            run = TrainingRun(
                model_key="fdshield-fraud-detector",
                dataset_version_id=dataset.id,
                status="CANDIDATE",
                model_version="2",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers={"X-MLOps-Admin-Token": "admin-secret"},
            json={"decision": "REJECT", "reason": "오탐 증가"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["training_run"]["status"], "REJECTED")
        self.admin.create_model_revision.assert_not_called()

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
