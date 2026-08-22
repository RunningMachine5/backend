import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.core.db import get_session
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.mlops import DatasetVersion, TrainingRun
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_features import MLTransactionFeatures
from app.services.features.ml_feature_assembler import build_transaction_fields
from app.services.mlops.mlflow import get_mlflow_registry_client
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data


class ModelCatalogApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        DatasetVersion.__table__.create(self.engine)
        TrainingRun.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        MLPredictionResult.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        self.mlflow = Mock()
        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_mlflow_registry_client] = lambda: self.mlflow
        self.client = TestClient(app)
        self.headers = {"X-MLOps-Admin-Token": "admin-secret"}
        self._seed_models_and_transactions()

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _transaction(self, transaction_id: int) -> Transaction:
        features = MLTransactionFeatures.model_validate(valid_ml_raw_data())
        values = build_transaction_fields(features)
        values["transaction_datetime"] = datetime(2026, 8, 22, 10, transaction_id)
        return Transaction(
            id=transaction_id,
            customer_id=None,
            source_account_number=f"source-{transaction_id}",
            recipient_account_number=f"recipient-{transaction_id}",
            **values,
        )

    def _seed_models_and_transactions(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            old_dataset = DatasetVersion(
                version="train_v41",
                gcs_uri="gs://bucket/train_v41.csv",
                row_count=100,
                created_at=now - timedelta(days=1),
            )
            current_dataset = DatasetVersion(
                version="train_v42",
                gcs_uri="gs://bucket/train_v42.csv",
                row_count=120,
                created_at=now,
            )
            session.add(old_dataset)
            session.add(current_dataset)
            session.commit()
            session.refresh(old_dataset)
            session.refresh(current_dataset)

            old_run = TrainingRun(
                model_key="fraud-model",
                dataset_version_id=old_dataset.id,
                mlflow_run_id="run-41",
                status="PRODUCTION",
                created_at=now - timedelta(days=1),
            )
            current_run = TrainingRun(
                model_key="fraud-model",
                dataset_version_id=current_dataset.id,
                mlflow_run_id="run-42",
                status="PRODUCTION",
                created_at=now,
            )
            session.add(old_run)
            session.add(current_run)
            session.add(self._transaction(1))
            session.add(self._transaction(2))
            session.add(self._transaction(3))
            session.commit()

            session.add(
                MLPredictionResult(
                    transaction_id=1,
                    predict_result=True,
                    predict_proba=0.91,
                    model_name="fraud-model",
                    model_version="41",
                    latency_ms=10,
                    created_at=now - timedelta(minutes=3),
                )
            )
            session.add(
                MLPredictionResult(
                    transaction_id=2,
                    predict_result=True,
                    predict_proba=0.82,
                    model_name="fraud-model",
                    model_version="41",
                    latency_ms=20,
                    created_at=now - timedelta(minutes=2),
                )
            )
            session.add(
                MLPredictionResult(
                    transaction_id=3,
                    predict_result=False,
                    predict_proba=0.18,
                    model_name="fraud-model",
                    model_version="42",
                    latency_ms=12,
                    created_at=now - timedelta(minutes=1),
                )
            )
            session.add(TransactionLabel(transaction_id=1, confirmed_is_fraud=True))
            session.add(TransactionLabel(transaction_id=2, confirmed_is_fraud=False))
            session.commit()

        self.mlflow.model_versions_by_run.return_value = {
            "run-41": "41",
            "run-42": "42",
        }

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_model_list_combines_registry_and_labeled_usage(self) -> None:
        response = self.client.get("/mlops/models", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        current, retired = response.json()
        self.assertEqual(current["model_version"], "42")
        self.assertEqual(current["status"], "PRODUCTION")
        self.assertEqual(current["usage"]["processed_transaction_count"], 1)
        self.assertIsNone(current["usage"]["label_agreement_percent"])

        self.assertEqual(retired["model_version"], "41")
        self.assertEqual(retired["status"], "RETIRED")
        self.assertEqual(retired["usage"]["processed_transaction_count"], 2)
        self.assertEqual(retired["usage"]["fraud_prediction_count"], 2)
        self.assertEqual(retired["usage"]["labeled_transaction_count"], 2)
        self.assertEqual(retired["usage"]["label_agreement_percent"], 50.0)
        self.assertEqual(retired["usage"]["false_positive_count"], 1)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_model_transactions_can_show_only_label_mismatches(self) -> None:
        response = self.client.get("/mlops/models", headers=self.headers)
        retired = response.json()[1]
        self.mlflow.resolve_model_version.return_value = "41"

        transactions = self.client.get(
            f"/mlops/models/{retired['training_run_id']}/transactions",
            params={"label_filter": "MISMATCH"},
            headers=self.headers,
        )

        self.assertEqual(transactions.status_code, 200, transactions.text)
        body = transactions.json()
        self.assertEqual(body["total_count"], 1)
        self.assertEqual(body["items"][0]["transaction_id"], 2)
        self.assertFalse(body["items"][0]["confirmed_is_fraud"])
        self.assertFalse(body["items"][0]["label_matches"])


if __name__ == "__main__":
    unittest.main()
