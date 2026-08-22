import os
import unittest
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.data.model.transaction import Transaction
from app.dto.ml_features import MLTransactionFeatures
from app.services.features.ml_feature_assembler import (
    build_account_fields,
    build_customer_fields,
    build_derived_features_fields,
    build_transaction_fields,
)
from app.services.mlops.cloud_run import (
    CloudRunAdminError,
    get_cloud_run_admin_client,
)
from app.services.mlops.dataset_builder import (
    DatasetBuildResult,
    DatasetLabelSummary,
    get_labeled_dataset_builder,
)
from app.services.mlops.mlflow import MLflowRegistryError, get_mlflow_registry_client
from app.services.mlops.model_review import (
    ModelReviewResult,
    get_model_review_llm,
)
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
        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        DerivedFeatures.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        self.cloud_run = Mock()
        self.dataset_builder = Mock()
        self.mlflow = Mock()
        self.model_reviewer = Mock()
        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_cloud_run_admin_client] = lambda: self.cloud_run
        app.dependency_overrides[get_labeled_dataset_builder] = (
            lambda: self.dataset_builder
        )
        app.dependency_overrides[get_mlflow_registry_client] = lambda: self.mlflow
        app.dependency_overrides[get_model_review_llm] = lambda: self.model_reviewer
        self.client = TestClient(app)
        self.headers = {"X-MLOps-Admin-Token": "admin-secret"}
        self.make_verification_transaction()

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

    def make_verification_transaction(self) -> None:
        """후보 모델 smoke에 사용할 최근 저장 거래를 만든다."""

        features = MLTransactionFeatures.model_validate(valid_ml_raw_data())
        customer = Customer(
            id=900001,
            name="검증 고객",
            identification_number="verification-customer",
            **build_customer_fields(features),
        )
        source = Account(
            id=900001,
            customer_id=customer.id,
            account_number="verification-source",
            **build_account_fields(features),
        )
        recipient = Account(
            id=900002,
            account_number="verification-recipient",
        )
        transaction = Transaction(
            id=900001,
            customer_id=customer.id,
            source_account_number=source.account_number,
            recipient_account_number=recipient.account_number,
            **build_transaction_fields(features),
        )
        derived = DerivedFeatures(
            id=transaction.id,
            **build_derived_features_fields(features),
        )
        with Session(self.engine) as session:
            session.add(customer)
            session.add(source)
            session.add(recipient)
            session.add(transaction)
            session.add(derived)
            session.commit()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_platform_status_checks_database_connection(self) -> None:
        response = self.client.get("/mlops/platform/status", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["backend_status"], "UP")
        self.assertEqual(response.json()["database_status"], "UP")
        self.assertIsInstance(response.json()["database_latency_ms"], float)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_execution_returns_runtime_details(self) -> None:
        run_id = self.make_run()
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            run.cloud_run_execution_name = "training-abc12"
            session.add(run)
            session.commit()
        self.cloud_run.get_training_execution.return_value = {
            "name": (
                "projects/p/locations/asia-northeast3/jobs/fdshield-training/"
                "executions/training-abc12"
            ),
            "createTime": "2026-08-19T03:00:00Z",
            "startTime": "2026-08-19T03:00:05Z",
            "completionTime": "2026-08-19T03:02:00Z",
            "runningCount": 0,
            "succeededCount": 1,
            "failedCount": 0,
            "cancelledCount": 0,
            "retriedCount": 0,
            "logUri": "https://console.cloud.google.com/logs/query",
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        }
        self.cloud_run.training_execution_outcome.return_value = "SUCCEEDED"

        response = self.client.get(
            f"/mlops/training/runs/{run_id}/execution",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["outcome"], "SUCCEEDED")
        self.assertEqual(response.json()["succeeded_count"], 1)
        self.assertEqual(
            response.json()["log_uri"],
            "https://console.cloud.google.com/logs/query",
        )
        self.cloud_run.get_training_execution.assert_called_once_with(
            "training-abc12"
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_build_generates_version_and_gcs_uri(self) -> None:
        self.dataset_builder.label_summary.return_value = DatasetLabelSummary(
            normal_count=9,
            fraud_count=3,
        )
        self.dataset_builder.build.return_value = DatasetBuildResult(
            source_row_count=200_000,
            output_row_count=200_012,
            confirmed_label_count=12,
            appended_label_count=12,
            normal_count=9,
            fraud_count=3,
        )

        response = self.client.post(
            "/mlops/datasets/build",
            headers=self.headers,
            json={"period_start": "2026-08-01", "period_end": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertRegex(
            created["version"],
            r"^train_v1-labeled-20260801-20260831-n9-f3-\d{8}T\d{6}Z$",
        )
        self.assertEqual(
            created["gcs_uri"],
            "gs://fdshield-ml-data-801817539291/versions/"
            f"{created['version']}.csv",
        )
        self.dataset_builder.build.assert_called_once_with(
            ANY,
            destination_uri=created["gcs_uri"],
            period_start=ANY,
            period_end=ANY,
        )
        self.assertEqual(created["period_start"], "2026-08-01")
        self.assertEqual(created["period_end"], "2026-08-31")
        self.assertEqual(created["period_normal_count"], 9)
        self.assertEqual(created["period_fraud_count"], 3)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_build_increments_version_number(self) -> None:
        with Session(self.engine) as session:
            session.add(
                DatasetVersion(
                    version="legacy-dataset",
                    gcs_uri="gs://bucket/legacy.csv",
                    row_count=100,
                )
            )
            session.commit()

        self.dataset_builder.label_summary.return_value = DatasetLabelSummary(
            normal_count=1,
            fraud_count=1,
        )
        self.dataset_builder.build.return_value = DatasetBuildResult(
            source_row_count=100,
            output_row_count=102,
            confirmed_label_count=2,
            appended_label_count=2,
            normal_count=1,
            fraud_count=1,
        )

        response = self.client.post(
            "/mlops/datasets/build",
            headers=self.headers,
            json={"period_start": "2026-08-01", "period_end": "2026-08-20"},
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertRegex(
            response.json()["version"],
            r"^train_v2-labeled-20260801-20260820-n1-f1-\d{8}T\d{6}Z$",
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_preview_returns_period_label_counts(self) -> None:
        with patch(
            "app.api.mlops.LabeledDatasetBuilder.label_summary",
            return_value=DatasetLabelSummary(normal_count=9, fraud_count=3),
        ):
            response = self.client.post(
                "/mlops/datasets/preview",
                headers=self.headers,
                json={"period_start": "2026-08-01", "period_end": "2026-08-31"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "base_period_start": "2026-01-01",
                "base_period_end": "2026-07-31",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "labeled_count": 12,
                "normal_count": 9,
                "fraud_count": 3,
            },
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_delete_removes_gcs_object_and_database_row(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="deletable-dataset",
                gcs_uri="gs://bucket/versions/deletable.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            assert dataset.id is not None
            dataset_id = dataset.id

        response = self.client.delete(
            f"/mlops/datasets/{dataset_id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 204, response.text)
        self.dataset_builder.delete_dataset.assert_called_once_with(
            "gs://bucket/versions/deletable.csv"
        )
        with Session(self.engine) as session:
            self.assertIsNone(session.get(DatasetVersion, dataset_id))

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_dataset_delete_rejects_version_used_by_training(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="used-dataset",
                gcs_uri="gs://bucket/versions/used.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            assert dataset.id is not None
            run = TrainingRun(
                model_key="fdshield-fraud-detector-v2",
                dataset_version_id=dataset.id,
                status="RUNNING",
            )
            session.add(run)
            session.commit()
            dataset_id = dataset.id

        response = self.client.delete(
            f"/mlops/datasets/{dataset_id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.dataset_builder.delete_dataset.assert_not_called()

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
    def test_training_execute_stores_execution_not_lro_operation_name(self) -> None:
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

        prepared = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )
        self.assertEqual(prepared.status_code, 201, prepared.text)
        run_id = prepared.json()["id"]

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
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
    def test_training_prepare_returns_requested_run_without_cloud_run(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="prepared-source",
                gcs_uri="gs://bucket/prepared.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id

        response = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "REQUESTED")
        self.cloud_run.run_training.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_prepared_training_executes_only_once(self) -> None:
        with Session(self.engine) as session:
            dataset = DatasetVersion(
                version="execute-source",
                gcs_uri="gs://bucket/execute.csv",
                row_count=100,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            dataset_id = dataset.id

        prepared = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )
        run_id = prepared.json()["id"]
        self.cloud_run.training_execution_name.return_value = "training-exec-prepared"
        self.cloud_run.run_training.return_value = {
            "name": "projects/p/locations/r/operations/prepared-op",
            "metadata": {
                "target": (
                    "projects/p/locations/r/jobs/training/"
                    "executions/training-exec-prepared"
                )
            },
        }

        started = self.client.post(
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
        )
        duplicate = self.client.post(
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
        )

        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["training_run"]["status"], "RUNNING")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.cloud_run.run_training.assert_called_once_with(
            min_pr_auc=0.0,
            min_recall=0.0,
            dataset_uri="gs://bucket/execute.csv",
            training_run_id=run_id,
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_execute_does_not_regress_an_early_callback(self) -> None:
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

        prepared = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )
        self.assertEqual(prepared.status_code, 201, prepared.text)
        run_id = prepared.json()["id"]

        def finish_before_jobs_run_returns(**_kwargs: object) -> dict[str, object]:
            with Session(self.engine) as callback_session:
                run = callback_session.get(TrainingRun, run_id)
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
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "CANDIDATE")
        self.assertEqual(response.json()["training_run"]["mlflow_run_id"], "early-run")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_unknown_training_acceptance_stays_running_for_late_callback(
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

        prepared = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )
        self.assertEqual(prepared.status_code, 201, prepared.text)
        run_id = prepared.json()["id"]
        self.cloud_run.run_training.side_effect = CloudRunAdminError(
            "Cloud Run 응답 timeout",
            request_may_have_been_accepted=True,
        )

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            self.assertEqual(run.status, "RUNNING")

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

        prepared = self.client.post(
            "/mlops/training/runs/prepare",
            headers=self.headers,
            json={"dataset_version_id": dataset_id},
        )
        self.assertEqual(prepared.status_code, 201, prepared.text)
        run_id = prepared.json()["id"]
        self.cloud_run.run_training.side_effect = CloudRunAdminError(
            "Cloud Run 요청 거절",
            status_code=400,
        )

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/execute",
            headers=self.headers,
            json={},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            self.assertEqual(run.status, "FAILED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_running_callback_connects_cloud_run_execution_without_finishing_run(
        self,
    ) -> None:
        run_id = self.make_run()
        payload = {
            "status": "RUNNING",
            "cloud_run_execution_name": "fdshield-training-started1",
        }

        first = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json=payload,
        )
        second = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json=payload,
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["status"], "RUNNING")
        self.assertEqual(
            second.json()["cloud_run_execution_name"],
            "fdshield-training-started1",
        )
        self.assertIsNone(second.json()["mlflow_run_id"])

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_success_callback_rejects_legacy_fields_and_is_idempotent(self) -> None:
        run_id = self.make_run()
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            assert run is not None
            run.error_message = "stale failure"
            session.add(run)
            session.commit()

        obsolete = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json={
                "status": "SUCCEEDED",
                "mlflow_run_id": "run-candidate",
                "model_version": "17",
            },
        )
        self.assertEqual(obsolete.status_code, 422)

        current_payload = {
            "status": "SUCCEEDED",
            "mlflow_run_id": "run-candidate",
            "cloud_run_execution_name": "fdshield-training-abc12",
        }

        first = self.client.post(
            f"/mlops/training/runs/{run_id}/result",
            headers=self.headers,
            json=current_payload,
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
        self.assertIsNone(second.json()["error_message"])
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
            self.assertEqual(response.json()["error_message"], "sanitized")

        with Session(self.engine) as session:
            failed_run = session.get(TrainingRun, failed_run_id)
            self.assertIsNotNone(failed_run)
            self.assertEqual(failed_run.error_message, "sanitized")

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
        self.cloud_run.stage_model_revision.return_value = {
            "operation": None,
            "tag": "model-v17",
            "revision": "serving-00017-candidate",
            "previousTraffic": [],
            "reused": True,
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
        self.cloud_run.stage_model_revision.assert_called_once_with("17")
        self.mlflow.set_model_version_tags.assert_called_once_with(
            "fdshield-fraud-detector-v2",
            "17",
            {
                "backend_decision": "APPROVE",
                "backend_decision_reason": "metrics checked in MLflow",
            },
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_staged_candidate_can_be_revalidated_without_new_db_columns(self) -> None:
        run_id = self.make_run("STAGED", "candidate-restage")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.stage_model_revision.return_value = {
            "operation": None,
            "tag": "model-v17",
            "revision": "serving-00017-candidate",
            "previousTraffic": [],
            "reused": True,
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
        self.assertIsNone(response.json()["operation_id"])
        self.cloud_run.stage_model_revision.assert_called_once_with("17")

        duplicate_retry = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE"},
        )
        self.assertEqual(duplicate_retry.status_code, 409)
        self.cloud_run.stage_model_revision.assert_called_once_with("17")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_approval_reuses_prepared_revision_without_operation(self) -> None:
        run_id = self.make_run("CANDIDATE", "candidate-prestaged")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.stage_model_revision.return_value = {
            "operation": None,
            "tag": "model-v17",
            "revision": "serving-00017-candidate",
            "image": f"registry/serving@sha256:{'a' * 64}",
            "taggedUrl": "https://model-v17---serving.run.app",
            "previousTraffic": [
                {
                    "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                    "revision": "serving-00016-live",
                    "percent": 100,
                }
            ],
            "reused": True,
        }

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE"},
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "STAGED")
        self.assertIsNone(response.json()["operation_id"])
        self.assertTrue(response.json()["reused"])
        self.cloud_run.stage_model_revision.assert_called_once_with("17")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_staging_validation_error_keeps_candidate_status(self) -> None:
        run_id = self.make_run("CANDIDATE", "candidate-reconciling")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.stage_model_revision.side_effect = CloudRunAdminError(
            "Serving Service가 아직 리비전을 준비 중입니다."
        )

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE"},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.status, "CANDIDATE")
        self.mlflow.set_model_version_tags.assert_not_called()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_approval_tag_write_failure_keeps_candidate_status(self) -> None:
        run_id = self.make_run("CANDIDATE", "candidate-tag-failure")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.stage_model_revision.return_value = {
            "operation": None,
            "tag": "model-v17",
            "revision": "serving-00017-candidate",
            "image": f"registry/serving@sha256:{'a' * 64}",
            "taggedUrl": "https://model-v17---serving.run.app",
            "previousTraffic": [],
            "reused": True,
        }
        self.mlflow.set_model_version_tags.side_effect = MLflowRegistryError(
            "MLflow tag write failed"
        )

        response = self.client.post(
            f"/mlops/training/runs/{run_id}/decision",
            headers=self.headers,
            json={"decision": "APPROVE", "reason": "reviewed"},
        )

        self.assertEqual(response.status_code, 502, response.text)
        self.cloud_run.stage_model_revision.assert_called_once_with("17")
        self.mlflow.set_model_version_tags.assert_called_once_with(
            "fdshield-fraud-detector-v2",
            "17",
            {
                "backend_decision": "APPROVE",
                "backend_decision_reason": "reviewed",
            },
        )
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.status, "CANDIDATE")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_retired_model_can_be_prepared_again(self) -> None:
        previous_run_id = self.make_run("RETIRED", "previous-production")
        current_run_id = self.make_run("PRODUCTION", "current-production")
        self.mlflow.resolve_model_version.return_value = "16"
        self.cloud_run.stage_model_revision.return_value = {
            "operation": {"name": "projects/p/locations/r/operations/reactivate"},
            "tag": "model-v16",
            "reused": False,
        }

        response = self.client.post(
            f"/mlops/training/runs/{previous_run_id}/reactivate",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "STAGED")
        self.assertEqual(response.json()["model_version"], "16")
        self.cloud_run.stage_model_revision.assert_called_once_with("16")
        with Session(self.engine) as session:
            current_run = session.get(TrainingRun, current_run_id)
            self.assertIsNotNone(current_run)
            self.assertEqual(current_run.status, "PRODUCTION")

        current_response = self.client.post(
            f"/mlops/training/runs/{current_run_id}/reactivate",
            headers=self.headers,
        )
        self.assertEqual(current_response.status_code, 409, current_response.text)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_promotion_requires_run_and_rejects_client_model_version(self) -> None:
        missing_run = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={},
        )
        bypass = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={
                "training_run_id": 1,
                "model_version": "999",
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
        previous_production_id = self.make_run("PRODUCTION", "production-run")
        run_id = self.make_run("STAGED", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"

        def promote_after_db_transition(**_kwargs):
            with Session(self.engine) as session:
                promoting_run = session.get(TrainingRun, run_id)
                self.assertIsNotNone(promoting_run)
                self.assertEqual(promoting_run.status, "PROMOTING")
            return {
                "operation": {"name": "projects/p/locations/r/operations/promote"},
                "revision": "serving-00017",
                "smokePrediction": {},
            }

        self.cloud_run.promote_model_revision.side_effect = promote_after_db_transition
        promoted = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={"training_run_id": run_id},
        )
        self.assertEqual(promoted.status_code, 202)
        self.assertEqual(promoted.json()["training_run"]["status"], "PROMOTING")
        promotion_request = self.cloud_run.promote_model_revision.call_args.kwargs
        self.assertEqual(promotion_request["model_version"], "17")
        self.assertEqual(promotion_request["transaction_id"], 900001)
        self.assertEqual(
            len(promotion_request["features"]),
            len(valid_ml_raw_data()),
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
        with Session(self.engine) as session:
            previous_production = session.get(TrainingRun, previous_production_id)
            self.assertIsNotNone(previous_production)
            self.assertEqual(previous_production.status, "RETIRED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_definitive_promotion_failure_is_marked_retryable(self) -> None:
        run_id = self.make_run("STAGED", "candidate-rejected-promotion")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.promote_model_revision.side_effect = CloudRunAdminError(
            "Cloud Run이 트래픽 변경을 거절했습니다.",
            status_code=400,
        )

        response = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={"training_run_id": run_id},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.status, "DEPLOYMENT_FAILED")

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_unknown_promotion_result_stays_promoting_for_live_reconcile(self) -> None:
        run_id = self.make_run("STAGED", "candidate-unknown-promotion")
        self.mlflow.resolve_model_version.return_value = "17"
        self.cloud_run.promote_model_revision.side_effect = CloudRunAdminError(
            "Cloud Run 응답이 유실됐습니다.",
            request_may_have_been_accepted=True,
        )

        response = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={"training_run_id": run_id},
        )

        self.assertEqual(response.status_code, 502, response.text)
        with Session(self.engine) as session:
            run = session.get(TrainingRun, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.status, "PROMOTING")

        duplicate = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={"training_run_id": run_id},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.cloud_run.promote_model_revision.assert_called_once()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_live_completion_recovers_a_legacy_staged_mismatch(self) -> None:
        run_id = self.make_run("STAGED", "candidate-live-but-staged")
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

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["training_run"]["status"], "PRODUCTION")
        self.mlflow.set_model_alias.assert_called_once_with(
            "fdshield-fraud-detector-v2",
            "champion",
            "17",
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_promotion_requires_a_stored_verification_transaction(self) -> None:
        run_id = self.make_run("STAGED", "candidate-run")
        self.mlflow.resolve_model_version.return_value = "17"
        with Session(self.engine) as session:
            derived = session.get(DerivedFeatures, 900001)
            assert derived is not None
            session.delete(derived)
            session.commit()

        response = self.client.post(
            "/mlops/serving/promotions",
            headers=self.headers,
            json={"training_run_id": run_id},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["error"]["message"],
            "자동 검증에 사용할 저장 거래가 없습니다.",
        )
        self.cloud_run.promote_model_revision.assert_not_called()

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
            json={"training_run_id": run_id},
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
    def test_ai_review_uses_candidate_and_production_metrics(self) -> None:
        production_id = self.make_run("PRODUCTION", "production-run")
        candidate_id = self.make_run("CANDIDATE", "candidate-run")
        details = {
            "candidate-run": {
                "model_version": "18",
                "metrics": {"validation_pr_auc": 0.82},
                "tags": {"promotion_recommendation": "RECOMMENDED"},
            },
            "production-run": {
                "model_version": "17",
                "metrics": {"validation_pr_auc": 0.92},
                "tags": {},
            },
        }
        self.mlflow.get_model_details.side_effect = (
            lambda _model_name, run_id: details[run_id]
        )
        self.model_reviewer.review.return_value = ModelReviewResult(
            decision="NOT_RECOMMENDED",
            summary="PR-AUC가 운영 모델보다 낮아 승격을 비추천합니다.",
        )

        response = self.client.post(
            f"/mlops/training/runs/{candidate_id}/ai-review",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "AI")
        self.assertEqual(response.json()["decision"], "NOT_RECOMMENDED")
        self.model_reviewer.review.assert_called_once_with(
            candidate_run_id=candidate_id,
            candidate_details=details["candidate-run"],
            production_run_id=production_id,
            production_details=details["production-run"],
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
