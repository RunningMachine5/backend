import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.core.db import get_session
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.dto.ml_prediction import (
    RAW_TRANSACTION_FEATURE_COLUMNS,
    MLTransactionFeatures,
)
from app.services.ml_serving.client import (
    MLPredictionResponse,
    MLServingError,
    get_ml_serving_client,
)
from main import app
from tests.ml_feature_fixture import valid_ml_raw_data, valid_transaction_row


class SuccessfulMLStub:
    """Backend의 ML Serving 요청·응답 계약을 검증하는 성공 테스트 대역."""

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
        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        MLPredictionResult.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)
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

    def test_create_transaction_saves_ml_serving_response(self) -> None:
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
                **valid_transaction_row(
                    "TX_STUB_001",
                    confirmed_is_fraud=True,
                ),
                "Customer_birth_date": "1981-06-15",
                **raw_data,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertTrue(body["ml_is_fraud"])
        self.assertEqual(body["fraud_probability"], 0.75)
        self.assertEqual(body["shap"], {"Transaction_Amount": 0.2})
        self.assertTrue(body["confirmed_is_fraud"])
        self.assertIsNotNone(body["labeled_at"])
        self.assertEqual(ml_stub.last_transaction_id, "TX_STUB_001")
        self.assertEqual(ml_stub.last_features, normalized_raw_data)

        with Session(self.engine) as session:
            stored = session.exec(select(Transaction)).one()
            self.assertEqual(stored.raw_features, normalized_raw_data)
            self.assertEqual(stored.customer_id, "C000494")
            self.assertEqual(stored.transaction_amount, 3_995_050)

            customer = session.exec(select(Customer)).one()
            self.assertEqual(customer.customer_id, stored.customer_id)
            self.assertEqual(customer.birth_date.isoformat(), "1981-06-15")

            accounts = session.exec(select(Account)).all()
            self.assertEqual(len(accounts), 2)
            self.assertEqual(
                {account.account_id for account in accounts},
                {stored.source_account_id, stored.recipient_account_id},
            )

            label = session.exec(select(TransactionLabel)).one()
            self.assertEqual(label.transaction_id, stored.transaction_id)
            self.assertTrue(label.confirmed_is_fraud)

            prediction_result = session.exec(select(MLPredictionResult)).one()
            self.assertEqual(prediction_result.transaction_id, "TX_STUB_001")
            self.assertTrue(prediction_result.prediction_is_fraud)
            self.assertEqual(prediction_result.fraud_probability, 0.75)
            self.assertEqual(
                prediction_result.model_name,
                "fdshield-rule-based-stub",
            )
            self.assertGreaterEqual(prediction_result.latency_ms, 0)

            score_results = session.exec(select(FraudTypeScoreResult)).all()
            self.assertEqual(score_results, [])
            self.assertIsNone(body["rule_scores"])

    def test_label_api_creates_updates_and_returns_saved_label(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = (
            lambda: SuccessfulNormalMLStub()
        )
        transaction_id = "TX_LABEL_API_001"
        created = self.client.post(
            "/transactions",
            json=valid_transaction_row(transaction_id),
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertIsNone(created.json()["confirmed_is_fraud"])
        self.assertIsNone(created.json()["labeled_at"])

        labeled = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": True},
        )
        self.assertEqual(labeled.status_code, 200, labeled.text)
        self.assertEqual(labeled.json()["transaction_id"], transaction_id)
        self.assertTrue(labeled.json()["confirmed_is_fraud"])
        first_labeled_at = labeled.json()["labeled_at"]
        self.assertIsNotNone(first_labeled_at)

        repeated = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": True},
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["labeled_at"], first_labeled_at)

        updated = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": False},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(updated.json()["confirmed_is_fraud"])

        detail = self.client.get(f"/transactions/{transaction_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertFalse(detail.json()["confirmed_is_fraud"])
        self.assertEqual(
            detail.json()["labeled_at"],
            updated.json()["labeled_at"],
        )

        listed = self.client.get("/transactions")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertFalse(listed.json()[0]["confirmed_is_fraud"])

        with Session(self.engine) as session:
            labels = session.exec(select(TransactionLabel)).all()
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0].transaction_id, transaction_id)
            self.assertFalse(labels[0].confirmed_is_fraud)

    def test_label_api_returns_404_for_unknown_transaction(self) -> None:
        response = self.client.put(
            "/transactions/TX_UNKNOWN/label",
            json={"confirmed_is_fraud": True},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "거래를 찾을 수 없습니다.")

    def test_label_api_rejects_non_boolean_label(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = (
            lambda: SuccessfulNormalMLStub()
        )
        transaction_id = "TX_LABEL_INVALID_001"
        created = self.client.post(
            "/transactions",
            json=valid_transaction_row(transaction_id),
        )
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": "true"},
        )

        self.assertEqual(response.status_code, 422, response.text)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(TransactionLabel)).all(), [])

    def test_create_transaction_keeps_input_when_ml_call_fails(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = lambda: FailedMLStub()

        response = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX_STUB_FAILED_001"),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["prediction_status"], "FAILED")

        with Session(self.engine) as session:
            stored = session.exec(select(Transaction)).one()
            self.assertEqual(stored.transaction_id, "TX_STUB_FAILED_001")
            self.assertEqual(
                set(stored.raw_features),
                set(RAW_TRANSACTION_FEATURE_COLUMNS),
            )
            customer = session.exec(select(Customer)).one()
            self.assertEqual(customer.birth_date.isoformat(), "1981-01-01")
            prediction_results = session.exec(select(MLPredictionResult)).all()
            self.assertEqual(prediction_results, [])
            score_results = session.exec(select(FraudTypeScoreResult)).all()
            self.assertEqual(score_results, [])

    def test_create_transaction_rejects_duplicate_transaction_id(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = (
            lambda: SuccessfulNormalMLStub()
        )
        payload = {
            **valid_transaction_row("TX_DUPLICATE_001"),
        }

        first = self.client.post("/transactions", json=payload)
        duplicate = self.client.post("/transactions", json=payload)

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(
            duplicate.json()["detail"],
            "이미 존재하는 transaction_id입니다.",
        )

    def test_normal_prediction_does_not_create_rule_scores(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = (
            lambda: SuccessfulNormalMLStub()
        )

        response = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX_RULE_SKIPPED_001"),
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
            json=valid_transaction_row("TX_RULE_SCORED_001"),
        )

        self.assertEqual(transaction.status_code, 201, transaction.text)
        body = transaction.json()
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertEqual(len(body["rule_scores"]), 4)
        self.assertAlmostEqual(body["rule_scores"]["VOICE_PHISHING"], 0.0)

        with Session(self.engine) as session:
            score_result = session.exec(
                select(FraudTypeScoreResult).where(
                    FraudTypeScoreResult.transaction_id
                    == "TX_RULE_SCORED_001"
                )
            ).one()
            self.assertEqual(score_result.rule_set_id, draft_id)
            self.assertEqual(set(score_result.type_scores), {
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
            })
            self.assertEqual(
                score_result.matched_components["VOICE_PHISHING"],
                [],
            )

        detail = self.client.get("/transactions/TX_RULE_SCORED_001")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["rule_scores"], body["rule_scores"])

        listed = self.client.get("/transactions")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()[0]["rule_scores"], body["rule_scores"])

    def test_create_transaction_rejects_missing_ml_feature(self) -> None:
        row = valid_transaction_row("TX_MISSING_LOCATION")
        row.pop("Location")

        response = self.client.post(
            "/transactions",
            json=row,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Location", response.text)

    def test_create_transaction_rejects_obsolete_or_unknown_feature(self) -> None:
        raw_data = valid_ml_raw_data()
        raw_data["Transaction_Failure_Status"] = 0

        response = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX_OBSOLETE_FEATURE"),
                **raw_data,
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
