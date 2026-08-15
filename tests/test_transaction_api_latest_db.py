import os
import unittest
from datetime import UTC, datetime

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.api.transaction import router as transaction_router
from app.core.db import get_session
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel
from app.domain.enums import RiskGrade
from app.dto.agent import AgentInputDTO
from app.services.agent.task_runner import get_agent_task_runner
from app.services.ml_serving.client import MLPredictionResponse, get_ml_serving_client
from app.services.rules.defaults import DEFAULT_RULE_SET

app = FastAPI()
app.include_router(transaction_router)


class StubMLClient:
    """외부 ML 서버 없이 Pipeline 연결만 확인하는 테스트 대역."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_features: dict[str, object] | None = None
        self.predict_result = 0
        self.predict_proba = 0.1

    def predict(
        self,
        *,
        transaction_id: int,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        self.calls += 1
        self.last_features = features
        return MLPredictionResponse(
            transaction_id=transaction_id,
            predict_result=self.predict_result,
            predict_proba=self.predict_proba,
            shap_values={},
            model_name="fdshield-fraud-detector-v2",
            model_version="test",
        )


def valid_transaction_request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "customer_id": "C-DEV-001",
        "source_account_number": "12345678",
        "recipient_account_number": "87654321",
        "transaction_datetime": "2026-08-14T12:00:00+09:00",
        "transaction_amount": -75_000,
        "channel": "mobile",
        "type_general_automatic": "general",
        "access_medium": "a",
        "num_connection_failure": 0,
        "operating_system": "android",
        "ip_address": "203.0.113.10",
        "mac_address": "00:1A:2B:3C:4D:5E",
        "location_lat": 37.5665,
        "location_lon": 126.978,
        "customer_rooting_jailbreak_indicator": False,
        "customer_mobile_roaming_indicator": False,
        "customer_vpn_indicator": False,
        "customer_flag_terminal_malicious_behavior_1": False,
        "customer_flag_terminal_malicious_behavior_2": False,
        "customer_flag_terminal_malicious_behavior_3": False,
        "customer_flag_terminal_malicious_behavior_5": False,
        "customer_flag_terminal_malicious_behavior_6": False,
    }
    payload.update(overrides)
    return payload


class TransactionApiLatestDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Customer.__table__.create(self.engine)
        Account.__table__.create(self.engine)
        Transaction.__table__.create(self.engine)
        DerivedFeatures.__table__.create(self.engine)
        MLPredictionResult.__table__.create(self.engine)
        TransactionLabel.__table__.create(self.engine)
        FraudRuleSet.__table__.create(self.engine)
        FraudRule.__table__.create(self.engine)
        FraudRuleComponent.__table__.create(self.engine)
        FraudTypeScoreResult.__table__.create(self.engine)

        with Session(self.engine) as session:
            session.add(
                Customer(
                    id="C-DEV-001",
                    name="테스트 고객",
                    birth_date=datetime(1990, 1, 1, tzinfo=UTC),
                    gender="female",
                    identification_number="IDENTITY-DEV-001",
                    registration_datetime=datetime(2020, 1, 1, tzinfo=UTC),
                    credit_rating=5,
                    loan_type="b",
                )
            )
            active_rule_set = FraudRuleSet(
                version=1,
                status=FraudRuleSetStatus.ACTIVE,
            )
            session.add(active_rule_set)
            session.flush()
            assert active_rule_set.id is not None
            for rule_order, definition in enumerate(DEFAULT_RULE_SET.rules):
                rule = FraudRule(
                    rule_set_id=active_rule_set.id,
                    type_code=definition.type_code,
                    display_name=definition.display_name,
                    enabled=definition.enabled,
                    sort_order=rule_order,
                )
                session.add(rule)
                session.flush()
                assert rule.id is not None
                for component_order, component in enumerate(definition.components):
                    session.add(
                        FraudRuleComponent(
                            rule_id=rule.id,
                            component_key=component.component_key,
                            name=component.name,
                            condition_expression=dict(component.condition_expression),
                            weight=component.weight,
                            sort_order=component_order,
                        )
                    )
            session.commit()

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.ml_client = StubMLClient()
        self.agent_inputs: list[AgentInputDTO] = []
        app.dependency_overrides[get_ml_serving_client] = lambda: self.ml_client
        app.dependency_overrides[get_agent_task_runner] = (
            lambda: self.agent_inputs.append
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_slim_request_is_saved_with_generated_integer_id(self) -> None:
        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(),
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertIsInstance(body["transaction_id"], int)
        self.assertGreater(body["transaction_id"], 0)
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertIs(body["predict_result"], False)
        self.assertEqual(body["predict_proba"], 0.1)
        self.assertIsNone(body["rule_set_id"])
        self.assertIsNone(body["rule_scores"])
        self.assertEqual(self.ml_client.calls, 1)
        self.assertEqual(self.agent_inputs, [])
        assert self.ml_client.last_features is not None
        self.assertEqual(len(self.ml_client.last_features), 59)
        self.assertEqual(self.ml_client.last_features["distance"], 0.0)
        self.assertEqual(
            self.ml_client.last_features["transaction_history_with_the_account"],
            0,
        )

        with Session(self.engine) as session:
            transaction = session.get(Transaction, body["transaction_id"])
            self.assertIsNotNone(transaction)
            assert transaction is not None
            self.assertEqual(transaction.transaction_amount, -75_000)
            self.assertEqual(transaction.location_lat, 37.5665)
            self.assertEqual(transaction.location_lon, 126.978)
            derived = session.get(DerivedFeatures, transaction.id)
            self.assertIsNotNone(derived)
            assert derived is not None
            self.assertEqual(derived.distance, 0.0)
            self.assertEqual(derived.transaction_history_with_the_account, 0)
            self.assertEqual(len(session.exec(select(MLPredictionResult)).all()), 1)
            self.assertEqual(session.exec(select(FraudTypeScoreResult)).all(), [])

    def test_fraud_prediction_calculates_and_returns_rule_scores(self) -> None:
        self.ml_client.predict_result = 1
        self.ml_client.predict_proba = 0.91

        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(),
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertIs(body["predict_result"], True)
        self.assertEqual(body["predict_proba"], 0.91)
        self.assertEqual(body["rule_set_id"], 1)
        self.assertEqual(
            set(body["rule_scores"]),
            {
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
            },
        )
        self.assertEqual(len(self.agent_inputs), 1)
        agent_input = self.agent_inputs[0]
        self.assertEqual(agent_input.transaction_id, body["transaction_id"])
        self.assertEqual(agent_input.risk_score, 42)
        self.assertEqual(agent_input.risk_grade, RiskGrade.MEDIUM)

        with Session(self.engine) as session:
            score = session.exec(select(FraudTypeScoreResult)).one()
            self.assertEqual(score.transaction_id, body["transaction_id"])
            self.assertEqual(score.rule_filter_status, "APPLIED")
            self.assertEqual(
                agent_input.fraud_type_score_result_id,
                score.id,
            )

    def test_atm_transaction_allows_missing_customer_and_recipient(self) -> None:
        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(
                customer_id=None,
                recipient_account_number=None,
                channel="ATM",
            ),
        )

        self.assertEqual(response.status_code, 201, response.text)
        transaction_id = response.json()["transaction_id"]
        self.assertEqual(response.json()["prediction_status"], "COMPLETED")
        self.assertEqual(self.ml_client.calls, 1)
        assert self.ml_client.last_features is not None
        self.assertEqual(
            self.ml_client.last_features["recipient_account_number"],
            "unknown-recipient",
        )
        self.assertEqual(
            self.ml_client.last_features["customer_name"],
            "unknown-customer",
        )
        with Session(self.engine) as session:
            transaction = session.get(Transaction, transaction_id)
            self.assertIsNotNone(transaction)
            assert transaction is not None
            self.assertIsNone(transaction.customer_id)
            self.assertIsNone(transaction.recipient_account_number)
            self.assertEqual(transaction.channel, "atm")

    def test_missing_customer_uses_temporary_profile_for_ml(self) -> None:
        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(customer_id=None),
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["prediction_status"], "COMPLETED")
        assert self.ml_client.last_features is not None
        self.assertEqual(
            self.ml_client.last_features["customer_name"],
            "unknown-customer",
        )
        self.assertEqual(self.ml_client.last_features["customer_credit_rating"], 5)

    def test_unknown_customer_returns_404_without_partial_storage(self) -> None:
        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(customer_id="C-NOT-FOUND"),
        )

        self.assertEqual(response.status_code, 404, response.text)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Transaction)).all(), [])
            self.assertEqual(session.exec(select(Account)).all(), [])

    def test_existing_account_owned_by_another_customer_returns_409(self) -> None:
        with Session(self.engine) as session:
            session.add(
                Customer(
                    id="C-OTHER",
                    name="다른 고객",
                    birth_date=datetime(1991, 1, 1, tzinfo=UTC),
                    gender="male",
                    identification_number="IDENTITY-OTHER",
                    registration_datetime=datetime(2021, 1, 1, tzinfo=UTC),
                    credit_rating=4,
                    loan_type="a",
                )
            )
            session.add(
                Account(
                    id="12345678",
                    customer_id="C-OTHER",
                    account_number="12345678",
                )
            )
            session.commit()

        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(),
        )

        self.assertEqual(response.status_code, 409, response.text)

    def test_label_and_lookup_use_generated_integer_id(self) -> None:
        created = self.client.post(
            "/transactions",
            json=valid_transaction_request(),
        )
        transaction_id = created.json()["transaction_id"]

        labeled = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": True},
        )
        detail = self.client.get(f"/transactions/{transaction_id}")

        self.assertEqual(labeled.status_code, 200, labeled.text)
        self.assertIs(labeled.json()["confirmed_is_fraud"], True)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["transaction_id"], transaction_id)
        self.assertIs(detail.json()["confirmed_is_fraud"], True)

    def test_invalid_network_location_and_connection_values_return_422(self) -> None:
        cases = (
            {"ip_address": "999.1.1.1"},
            {"mac_address": "not-a-mac"},
            {"location_lat": 91},
            {"location_lon": -181},
            {"num_connection_failure": -1},
        )
        for invalid in cases:
            with self.subTest(invalid=invalid):
                response = self.client.post(
                    "/transactions",
                    json=valid_transaction_request(**invalid),
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_legacy_raw59_fields_are_rejected_by_dev_request_contract(self) -> None:
        response = self.client.post(
            "/transactions",
            json=valid_transaction_request(distance=1.5),
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("distance", response.text)


if __name__ == "__main__":
    unittest.main()
