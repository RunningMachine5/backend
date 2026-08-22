import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.api.mlops import router
from app.core.db import get_session
from app.data.model.ml_prediction_result import MLPredictionResult
from app.repositories.inference_performance import InferencePerformanceRepository


app = FastAPI()
app.include_router(router)


class InferencePerformanceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        MLPredictionResult.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.client = TestClient(app)
        self.headers = {"X-MLOps-Admin-Token": "admin-secret"}

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_recent_inference_count_and_p95_latency(self) -> None:
        empty = self.client.get(
            "/mlops/serving/performance",
            headers=self.headers,
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["inference_count"], 0)
        self.assertIsNone(empty.json()["p95_latency_ms"])

        now = datetime.now()
        with Session(self.engine) as session:
            for index in range(1, 21):
                session.add(
                    MLPredictionResult(
                        transaction_id=index,
                        predict_result=False,
                        predict_proba=0.1,
                        model_name="fdshield-fraud-detector-v2",
                        model_version="5",
                        latency_ms=index * 10,
                        created_at=now - timedelta(seconds=index),
                    )
                )
            session.add(
                MLPredictionResult(
                    transaction_id=99,
                    predict_result=False,
                    predict_proba=0.1,
                    model_name="fdshield-fraud-detector-v2",
                    model_version="4",
                    latency_ms=999,
                    created_at=now - timedelta(minutes=6),
                )
            )
            session.commit()

        response = self.client.get(
            "/mlops/serving/performance",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["window_minutes"], 5)
        self.assertEqual(body["inference_count"], 20)
        self.assertEqual(body["p95_latency_ms"], 190)
        self.assertIsNotNone(body["latest_inference_at"])

    def test_throughput_groups_normal_and_fraud_results(self) -> None:
        now = datetime.now()
        with Session(self.engine) as session:
            for transaction_id, is_fraud, seconds_ago in [
                (1, False, 10),
                (2, True, 20),
                (3, False, 70),
            ]:
                session.add(
                    MLPredictionResult(
                        transaction_id=transaction_id,
                        predict_result=is_fraud,
                        predict_proba=0.9 if is_fraud else 0.1,
                        model_name="fdshield-fraud-detector-v2",
                        model_version="5",
                        latency_ms=20,
                        created_at=now - timedelta(seconds=seconds_ago),
                    )
                )
            session.commit()

            summary = InferencePerformanceRepository(session).summarize_throughput(
                now - timedelta(minutes=5),
                60,
            )

        self.assertEqual(summary.completed_count, 3)
        self.assertEqual(summary.normal_count, 2)
        self.assertEqual(summary.fraud_count, 1)
        self.assertEqual(sum(point["value"] for point in summary.normal_series), 2)
        self.assertEqual(sum(point["value"] for point in summary.fraud_series), 1)


if __name__ == "__main__":
    unittest.main()
