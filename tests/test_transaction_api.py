import os
import unittest

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.transaction import Transaction
from app.dto.ml_prediction import (
    MLTransactionFeatures,
    RAW_TRANSACTION_FEATURE_COLUMNS,
)
from app.services.ml_serving.client import (
    MLPredictionResponse,
    MLServingError,
    get_ml_serving_client,
)
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data


class SuccessfulMLStub:
    """Backend가 현재 ML Stub 계약으로 호출하는지 확인하는 테스트 대역."""

    def __init__(self) -> None:
        self.last_transaction_id: str | None = None
        self.last_features: dict[str, object] | None = None

    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        self.last_transaction_id = transaction_id
        self.last_features = features
        return MLPredictionResponse(
            transaction_id=transaction_id,
            is_fraud=True,
            fraud_probability=0.75,
            shap={"Transaction_Amount": 0.2},
            model_name="fdshield-rule-based-stub",
            model_version="0",
        )


class FailedMLStub:
    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        raise MLServingError("테스트용 ML 서버 연결 실패")


class TransactionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Transaction.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_create_transaction_saves_ml_stub_response(self) -> None:
        ml_stub = SuccessfulMLStub()
        app.dependency_overrides[get_ml_serving_client] = lambda: ml_stub

        raw_data = valid_ml_raw_data()
        normalized_raw_data = MLTransactionFeatures.model_validate(raw_data).model_dump(
            mode="json",
            by_alias=True,
        )
        response = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_STUB_001",
                "occurred_at": "2026-08-05T12:00:00+09:00",
                "raw_data": raw_data,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertTrue(body["ml_is_fraud"])
        self.assertEqual(body["fraud_probability"], 0.75)
        self.assertEqual(body["shap"], {"Transaction_Amount": 0.2})
        self.assertEqual(ml_stub.last_transaction_id, "TX_STUB_001")
        self.assertEqual(ml_stub.last_features, normalized_raw_data)

        with Session(self.engine) as session:
            stored = session.exec(select(Transaction)).one()
            self.assertEqual(stored.raw_data, normalized_raw_data)
            self.assertEqual(stored.prediction_status, "COMPLETED")
            self.assertEqual(stored.model_name, "fdshield-rule-based-stub")

    def test_create_transaction_keeps_input_when_ml_call_fails(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = lambda: FailedMLStub()

        response = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_STUB_FAILED_001",
                "raw_data": valid_ml_raw_data(),
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["prediction_status"], "FAILED")

        with Session(self.engine) as session:
            stored = session.exec(select(Transaction)).one()
            self.assertEqual(stored.transaction_id, "TX_STUB_FAILED_001")
            self.assertEqual(
                set(stored.raw_data),
                set(RAW_TRANSACTION_FEATURE_COLUMNS),
            )
            self.assertIsNone(stored.fraud_probability)

    def test_create_transaction_rejects_missing_ml_feature(self) -> None:
        raw_data = valid_ml_raw_data()
        raw_data.pop("Location")

        response = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_MISSING_LOCATION",
                "raw_data": raw_data,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Location", response.text)

    def test_create_transaction_rejects_obsolete_or_unknown_feature(self) -> None:
        raw_data = valid_ml_raw_data()
        raw_data["Transaction_Failure_Status"] = 0

        response = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_OBSOLETE_FEATURE",
                "raw_data": raw_data,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Transaction_Failure_Status", response.text)

    def test_ml_raw_feature_contract_has_54_exact_columns(self) -> None:
        columns = set(RAW_TRANSACTION_FEATURE_COLUMNS)

        self.assertEqual(len(columns), 54)
        self.assertIn("Location", columns)
        self.assertIn("Time Difference", columns)
        self.assertNotIn("Time_difference", columns)
        self.assertNotIn("Transaction_Failure_Status", columns)
        self.assertNotIn("Customer_flag_terminal_malicious_behavior_4", columns)


if __name__ == "__main__":
    unittest.main()
