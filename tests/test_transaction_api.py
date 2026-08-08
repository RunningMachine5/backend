import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudTypeScoreResult,
)
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


class SuccessfulNormalMLStub:
    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        return MLPredictionResponse(
            transaction_id=transaction_id,
            is_fraud=False,
            fraud_probability=0.05,
            shap={},
            model_name="fdshield-rule-based-stub",
            model_version="0",
        )


class TransactionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Transaction.__table__.create(self.engine)
        FraudRuleSet.__table__.create(self.engine)
        FraudRule.__table__.create(self.engine)
        FraudRuleComponent.__table__.create(self.engine)
        FraudTypeScoreResult.__table__.create(self.engine)

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

            score_results = session.exec(select(FraudTypeScoreResult)).all()
            self.assertEqual(score_results, [])
            self.assertIsNone(body["rule_scores"])

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
            score_results = session.exec(select(FraudTypeScoreResult)).all()
            self.assertEqual(score_results, [])

    def test_normal_prediction_does_not_create_rule_scores(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = (
            lambda: SuccessfulNormalMLStub()
        )

        response = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_RULE_SKIPPED_001",
                "raw_data": valid_ml_raw_data(),
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["rule_scores"])
        with Session(self.engine) as session:
            score_results = session.exec(select(FraudTypeScoreResult)).all()
            self.assertEqual(score_results, [])

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_fraud_prediction_returns_and_saves_all_rule_scores(self) -> None:
        ml_stub = SuccessfulMLStub()
        app.dependency_overrides[get_ml_serving_client] = lambda: ml_stub
        headers = {"X-MLOps-Admin-Token": "admin-secret"}

        draft_response = self.client.post("/rule-sets/drafts", headers=headers)
        self.assertEqual(draft_response.status_code, 201, draft_response.text)
        draft_id = draft_response.json()["id"]
        activation = self.client.post(
            f"/rule-sets/{draft_id}/activate",
            headers=headers,
        )
        self.assertEqual(activation.status_code, 200, activation.text)

        transaction = self.client.post(
            "/transactions",
            json={
                "transaction_id": "TX_RULE_SCORED_001",
                "raw_data": valid_ml_raw_data(),
            },
        )

        self.assertEqual(transaction.status_code, 201, transaction.text)
        body = transaction.json()
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertEqual(len(body["rule_scores"]), 5)
        self.assertAlmostEqual(body["rule_scores"]["VOICE_PHISHING"], 0.70)

        with Session(self.engine) as session:
            score_result = session.exec(
                select(FraudTypeScoreResult).where(
                    FraudTypeScoreResult.transaction_id
                    == "TX_RULE_SCORED_001"
                )
            ).one()
            self.assertEqual(set(score_result.type_scores), {
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
                "CARD_FRAUD",
            })
            self.assertEqual(score_result.rule_set_version, 1)
            self.assertIn(
                "loan_related",
                score_result.matched_components["VOICE_PHISHING"],
            )

        detail = self.client.get("/transactions/TX_RULE_SCORED_001")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["rule_scores"], body["rule_scores"])

        listed = self.client.get("/transactions")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()[0]["rule_scores"], body["rule_scores"])

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
