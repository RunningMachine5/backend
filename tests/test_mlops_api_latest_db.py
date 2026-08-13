import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.services.mlops.cloud_run import (
    CloudRunAdminError,
    get_cloud_run_admin_client,
)
from app.services.mlops.mlflow import get_mlflow_registry_client
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data


class LatestDatabaseMLOpsApiTest(unittest.TestCase):
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

        self.cloud_run = Mock()
        self.mlflow = Mock()
        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_cloud_run_admin_client] = lambda: self.cloud_run
        app.dependency_overrides[get_mlflow_registry_client] = lambda: self.mlflow
        self.client = TestClient(app)
        self.headers = {"X-MLOps-Admin-Token": "admin-secret"}

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def make_run(self, status: str = "RUNNING", mlflow_run_id: str | None = None) -> int:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version=f"dataset-{status.lower()}-{mlflow_run_id or 'none'}",
                gcs_uri="gs://bucket/transactions.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            run = TrainingRun(
                model_key="fdshield-fraud-detector-v2",
                dataset_version_id=dataset.id,
                status=status,
                mlflow_run_id=mlflow_run_id,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            assert run.id is not None
            return run.id

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_admin_auth_error_uses_common_response(self) -> None:
        response = self.client.get("/mlops/training/runs")

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(
            response.json(),
            {
                "success": False,
                "data": None,
                "error": {
                    "code": "HTTP_401",
                    "message": "MLOps 관리 토큰이 올바르지 않습니다.",
                    "details": None,
                },
            },
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_start_stores_execution_not_lro_operation_name(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="training-source",
                gcs_uri="gs://bucket/training.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id
        self.cloud_run.training_execution_name.return_value = "training-exec-abc"
        self.cloud_run.run_training.return_value = {
            "name": "projects/p/locations/r/operations/train-op",
            "metadata": {
                "target": (
                    "projects/p/locations/r/jobs/training/"
                    "executions/training-exec-abc"
                )
            },
        }

        response = self.client.post(
            "/mlops/training/runs",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation_id"], "train-op")
        self.assertEqual(
            response.json()["training_run"]["cloud_run_execution_name"],
            "training-exec-abc",
        )
        self.cloud_run.run_training.assert_called_once_with(
            min_pr_auc=0.0,
            min_recall=0.0,
            dataset_uri="gs://bucket/training.csv",
            training_run_id=1,
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_start_does_not_regress_an_early_callback(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="early-callback-source",
                gcs_uri="gs://bucket/early.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id

        def finish_before_jobs_run_returns(**_kwargs: object) -> dict[str, object]:
            with Session(self.engine) as callback_session:
                run = callback_session.get(TrainingRun, 1)
                assert run is not None
                run.status = "CANDIDATE"
                run.mlflow_run_id = "early-run"
                run.cloud_run_execution_name = "training-early"
                callback_session.add(run)
                callback_session.commit()
            return {
                "name": "projects/p/locations/r/operations/train-op",
                "metadata": {
                    "target": (
                        "projects/p/locations/r/jobs/training/"
                        "executions/training-early"
                    )
                },
            }

        self.cloud_run.run_training.side_effect = finish_before_jobs_run_returns
        self.cloud_run.training_execution_name.return_value = "training-early"

        response = self.client.post(
            "/mlops/training/runs",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "CANDIDATE")
        self.assertEqual(response.json()["training_run"]["mlflow_run_id"], "early-run")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_unknown_training_acceptance_stays_requested_for_late_callback(
        self,
    ) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="unknown-acceptance-source",
                gcs_uri="gs://bucket/unknown-acceptance.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id
        self.cloud_run.run_training.side_effect = CloudRunAdminError(
            "Cloud Run 응답 timeout",
            request_may_have_been_accepted=True,
        )

        response = self.client.post(
            "/mlops/training/runs",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.exec(select(TrainingRun)).one()
            self.assertEqual(run.status, "REQUESTED")
            run_id = run.id
        self.assertIsNotNone(run_id)

        callback = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json={
                "status": "SUCCEEDED",
                "mlflow_run_id": "late-success-run",
                "cloud_run_execution_name": "training-late-success",
            },
        )
        self.assertEqual(callback.status_code, 200, callback.text)
        self.assertEqual(callback.json()["status"], "CANDIDATE")
        self.assertEqual(callback.json()["mlflow_run_id"], "late-success-run")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_definitive_training_rejection_marks_run_failed(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="rejected-start-source",
                gcs_uri="gs://bucket/rejected-start.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id
        self.cloud_run.run_training.side_effect = CloudRunAdminError(
            "Cloud Run 요청 거절",
            status_code=400,
        )

        response = self.client.post(
            "/mlops/training/runs",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.exec(select(TrainingRun)).one()
            self.assertEqual(run.status, "FAILED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_success_callback_is_legacy_compatible_and_idempotent(self) -> None:
        run_id = self.make_run()
        legacy_payload = {
            "status": "SUCCEEDED",
            "mlflow_run_id": "run-candidate",
            "cloud_run_execution_name": "fdshield-training-abc12",
            "model_version": "17",
            "comparison_result": {
                "candidate": {"model_version": "17", "metrics": {"pr_auc": 0.9}},
                "recommendation": "RECOMMENDED",
            },
        }

        first = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json=legacy_payload,
        )
        second = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json={
                "status": "SUCCEEDED",
                "mlflow_run_id": "run-candidate",
                "cloud_run_execution_name": "fdshield-training-abc12",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "CANDIDATE")
        self.assertEqual(second.json()["mlflow_run_id"], "run-candidate")
        self.assertEqual(
            second.json()["cloud_run_execution_name"],
            "fdshield-training-abc12",
        )
        self.assertNotIn("model_version", second.json())
        self.assertEqual(second.json()["model_details"]["source"], "MLFLOW")

        conflict = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json={"status": "SUCCEEDED", "mlflow_run_id": "different-run"},
        )
        self.assertEqual(conflict.status_code, 409)

        execution_conflict = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json={
                "status": "SUCCEEDED",
                "mlflow_run_id": "run-candidate",
                "cloud_run_execution_name": "fdshield-training-other",
            },
        )
        self.assertEqual(execution_conflict.status_code, 409)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_failed_callback_is_idempotent_and_cannot_overwrite_success(self) -> None:
        failed_run_id = self.make_run()
        for _ in range(2):
            response = self.client.post(
                f"/mlops/training/runs/{failed_run_id}/result",
                headers=self.headers,
                json={"status": "FAILED", "error_message": "sanitized"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "FAILED")

        candidate_run_id = self.make_run("CANDIDATE", "successful-run")
        conflict = self.client.post(
            f"/mlops/training/runs/{candidate_run_id}/result",
            headers=self.headers,
            json={"status": "FAILED"},
        )
        self.assertEqual(conflict.status_code, 409)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_approval_resolves_version_from_mlflow_run(self) -> None:
        run_id = self.make_run("CANDIDATE", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.create_model_revision.return_value = {
            "operation": {
                "name": "projects/p/locations/r/operations/create-revision"
            },
            "tag": "model-v17",
            "previousTraffic": [],
        }

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE", "reason": "metrics checked in MLflow"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["training_run"]["status"], "STAGED")
        self.assertEqual(response.json()["model_version"], "17")
        self.mlflow.resolve_model_version.assert_called_once_with(
            "fdshield-fraud-detector-v2", "candidate-run"
        )
        self.cloud_run.create_model_revision.assert_called_once_with("17")
        self.mlflow.set_model_version_tags.assert_called_once_with(
            "fdshield-fraud-detector-v2",
            "17",
            {
                "backend_decision": "APPROVE",
                "backend_decision_reason": "metrics checked in MLflow",
            },
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_failed_async_staging_can_be_restaged_without_new_db_columns(self) -> None:
        run_id = self.make_run("STAGED", "candidate-restage")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.create_model_revision.return_value = {
            "operation": {
                "name": "projects/p/locations/r/operations/restage-revision"
            },
            "tag": "model-v17",
            "previousTraffic": [],
        }

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={
                "decision": "APPROVE",
                "reason": "retry failed staging",
                "restage": True,
            },
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "STAGED")
        self.assertEqual(response.json()["operation_id"], "restage-revision")
        self.cloud_run.create_model_revision.assert_called_once_with("17")

        duplicate_retry = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE"},
        )
        self.assertEqual(duplicate_retry.status_code, 409)
        self.cloud_run.create_model_revision.assert_called_once_with("17")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_promotion_requires_run_and_rejects_client_model_version(self) -> None:
        missing_run = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={
                "transaction_id": "TX-SMOKE",
                "features": valid_ml_raw_data(),
            },
        )
        bypass = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={
                "training_run_id": 1,
                "model_version": "999",
                "transaction_id": "TX-SMOKE",
                "features": valid_ml_raw_data(),
            },
        )
        self.assertEqual(missing_run.status_code, 422)
        self.assertEqual(bypass.status_code, 422)
        self.cloud_run.promote_model_revision.assert_not_called()

        direct_revision = self.client.post(
            "/mlops/serving/revisions",
            headers=self.headers,
            json={"model_version": "999"},
        )
        self.assertEqual(direct_revision.status_code, 404)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_promotion_and_live_completion_use_resolved_version(self) -> None:
        run_id = self.make_run("STAGED", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.promote_model_revision.return_value = {
            "operation": {"name": "projects/p/locations/r/operations/promote"},
            "revision": "serving-00017",
            "smokePrediction": {},
        }
        promoted = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={
                "training_run_id": run_id,
                "transaction_id": "TX-SMOKE",
                "features": valid_ml_raw_data(),
            },
        )
        self.assertEqual(promoted.status_code, 202)
        self.assertEqual(promoted.json()["training_run"]["status"], "PROMOTING")
        self.cloud_run.promote_model_revision.assert_called_once()
        self.assertEqual(
            self.cloud_run.promote_model_revision.call_args.kwargs["model_version"],
            "17",
        )

        self.cloud_run.get_model_deployment_status.return_value = {
            "ready": True,
            "reason": None,
            "revision": "serving-00017",
            "trafficPercent": 100,
        }
        self.cloud_run.get_operation.return_value = {
            "name": "projects/p/locations/r/operations/promote",
            "done": True,
            "response": {},
        }
        completed = self.client.post(
            f"/mlops/training/runs/{run_id}/deployment/complete",
            headers=self.headers,
            json={"operation_id": "promote"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["training_run"]["status"], "PRODUCTION")
        self.mlflow.set_model_alias.assert_called_once_with(
            "fdshield-fraud-detector-v2", "champion", "17"
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_live_completion_keeps_promoting_when_traffic_is_not_ready(self) -> None:
        run_id = self.make_run("PROMOTING", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.get_model_deployment_status.return_value = {
            "ready": False,
            "reason": "트래픽이 아직 반영되지 않았습니다.",
        }
        self.cloud_run.get_operation.return_value = {
            "name": "projects/p/locations/r/operations/promote",
            "done": True,
            "response": {},
        }

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/deployment/complete",
            headers=self.headers,
            json={"operation_id": "promote"},
        )

        self.assertEqual(response.status_code, 409)
        self.mlflow.set_model_alias.assert_not_called()
        detail = self.client.get(
            f"/mlops/training/runs/{run_id}", headers=self.headers
        )
        self.assertEqual(detail.json()["status"], "PROMOTING")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_live_completion_recovers_without_persisted_operation_id(self) -> None:
        run_id = self.make_run("PROMOTING", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.get_model_deployment_status.return_value = {
            "ready": True,
            "reason": None,
            "revision": "serving-00017",
            "trafficPercent": 100,
        }

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/deployment/complete",
            headers=self.headers,
            json={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["training_run"]["status"], "PRODUCTION")
        self.cloud_run.get_operation.assert_not_called()
        self.mlflow.set_model_alias.assert_called_once_with(
            "fdshield-fraud-detector-v2", "champion", "17"
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_failed_promotion_operation_is_terminal_but_retryable(self) -> None:
        run_id = self.make_run("PROMOTING", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.get_operation.return_value = {
            "name": "projects/p/locations/r/operations/promote-failed",
            "done": True,
            "error": {"code": 13, "message": "traffic patch failed"},
        }

        failed = self.client.post(
            f"/mlops/training/runs/{run_id}/deployment/complete",
            headers=self.headers,
            json={"operation_id": "promote-failed"},
        )
        self.assertEqual(failed.status_code, 502)
        detail = self.client.get(
            f"/mlops/training/runs/{run_id}", headers=self.headers
        )
        self.assertEqual(detail.json()["status"], "DEPLOYMENT_FAILED")

        self.cloud_run.promote_model_revision.return_value = {
            "operation": {
                "name": "projects/p/locations/r/operations/promote-retry"
            },
            "revision": "serving-00017",
            "smokePrediction": {},
        }
        retried = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={
                "training_run_id": run_id,
                "transaction_id": "TX-SMOKE",
                "features": valid_ml_raw_data(),
            },
        )
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.json()["training_run"]["status"], "PROMOTING")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_model_details_are_read_from_mlflow(self) -> None:
        run_id = self.make_run("CANDIDATE", "candidate-run")
        self.mlflow.get_model_details.return_value = {
            "source": "MLFLOW",
            "run_id": "candidate-run",
            "model_name": "fdshield-fraud-detector-v2",
            "model_version": "17",
            "artifact_uri": "mlflow-artifacts:/1/candidate-run/artifacts",
            "model_comparison_artifact_path": "metadata/model-comparison.json",
            "metrics": {"validation_pr_auc": 0.95},
            "params": {"decision_threshold": "0.61"},
            "tags": {"promotion_recommendation": "RECOMMENDED"},
        }

        response = self.client.get(
            f"/mlops/training/runs/{run_id}/model-details",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "MLFLOW")
        self.assertEqual(response.json()["model_version"], "17")
        self.assertEqual(
            response.json()["artifact_uri"],
            "mlflow-artifacts:/1/candidate-run/artifacts",
        )
        self.assertEqual(
            response.json()["model_comparison_artifact_path"],
            "metadata/model-comparison.json",
        )
        self.mlflow.get_model_details.assert_called_once_with(
            "fdshield-fraud-detector-v2", "candidate-run"
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_reconcile_marks_terminal_failed_execution(self) -> None:
        run_id = self.make_run("RUNNING")
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            run.cloud_run_execution_name = "training-failed"
            session.add(run)
            session.commit()
        self.cloud_run.get_training_execution.return_value = {
            "name": "projects/p/locations/r/jobs/training/executions/training-failed",
            "terminalCondition": {"state": "CONDITION_FAILED"},
        }
        self.cloud_run.training_execution_outcome.return_value = "FAILED"

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/reconcile",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_outcome"], "FAILED")
        self.assertEqual(response.json()["training_run"]["status"], "FAILED")
        self.cloud_run.get_training_execution.assert_called_once_with(
            "training-failed"
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_reconcile_does_not_invent_mlflow_run_for_success(self) -> None:
        run_id = self.make_run("RUNNING")
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            run.cloud_run_execution_name = "training-success"
            session.add(run)
            session.commit()
        self.cloud_run.get_training_execution.return_value = {
            "name": "projects/p/locations/r/jobs/training/executions/training-success",
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        }
        self.cloud_run.training_execution_outcome.return_value = "SUCCEEDED"

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/reconcile",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 409, response.text)
        with Session(self.engine) as session:
            self.assertEqual(session.get(TrainingRun, run_id).status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
