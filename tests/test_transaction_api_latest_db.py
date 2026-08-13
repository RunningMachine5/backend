import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
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
from app.dto.ml_prediction import RAW_TRANSACTION_FEATURE_COLUMNS
from app.dto.transaction import TransactionCreateDTO
from app.repositories.transaction import TransactionRepository
from app.services.ml_serving.client import (
    MLPredictionResponse,
    MLServingError,
    get_ml_serving_client,
)
from app.services.rules.defaults import DEFAULT_RULE_SET
from tests.ml_feature_fixture import valid_transaction_row

app = FastAPI()
app.include_router(transaction_router)


class SuccessfulNormalMLClient:
    def __init__(
        self,
        *,
        is_fraud: bool = False,
        fraud_probability: float = 0.05,
    ) -> None:
        self.is_fraud = is_fraud
        self.fraud_probability = fraud_probability
        self.calls = 0
        self.last_transaction_id: str | None = None
        self.last_features: dict[str, object] | None = None

    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        self.calls += 1
        self.last_transaction_id = transaction_id
        self.last_features = features
        return MLPredictionResponse(
            transaction_id=transaction_id,
            predict_result=int(self.is_fraud),
            predict_proba=self.fraud_probability,
            shap_values={},
            model_name="fdshield-fraud-detector-v2",
            model_version="1",
        )


class FailingMLClient:
    def predict(
        self,
        *,
        transaction_id: str,
        features: dict[str, object],
    ) -> MLPredictionResponse:
        raise MLServingError("테스트용 ML Serving 장애")


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

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.ml_client = SuccessfulNormalMLClient()
        app.dependency_overrides[get_ml_serving_client] = lambda: self.ml_client
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _activate_default_rules(self) -> int:
        with Session(self.engine) as session:
            rule_set = FraudRuleSet(
                version=1,
                status=FraudRuleSetStatus.ACTIVE,
            )
            session.add(rule_set)
            session.flush()
            assert rule_set.id is not None

            for rule_order, definition in enumerate(DEFAULT_RULE_SET.rules):
                rule = FraudRule(
                    rule_set_id=rule_set.id,
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
            return rule_set.id

    def test_flat_raw64_contract_persists_prediction_and_reassembles(self) -> None:
        row = valid_transaction_row("TX-API-ROUNDTRIP")
        expected = TransactionCreateDTO.model_validate(row).raw_features.model_dump(
            mode="json",
            by_alias=True,
        )

        response = self.client.post("/transactions", json=row)

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["raw_features"], expected)
        self.assertEqual(body["prediction_status"], "COMPLETED")
        self.assertFalse(body["ml_is_fraud"])
        self.assertEqual(body["fraud_probability"], 0.05)
        self.assertEqual(body["model_name"], "fdshield-fraud-detector-v2")
        self.assertEqual(body["model_version"], "1")
        self.assertGreaterEqual(body["latency_ms"], 0)
        self.assertEqual(self.ml_client.last_transaction_id, "TX-API-ROUNDTRIP")
        self.assertEqual(self.ml_client.last_features, expected)
        self.assertEqual(len(expected), 59)
        self.assertEqual(set(expected), set(RAW_TRANSACTION_FEATURE_COLUMNS))

        with Session(self.engine) as session:
            transaction = session.exec(select(Transaction)).one()
            assembled = TransactionRepository(session).load_ml_features(transaction)
            self.assertIsNotNone(assembled)
            self.assertEqual(
                assembled.model_dump(mode="json", by_alias=True),
                expected,
            )
            prediction = session.exec(select(MLPredictionResult)).one()
            self.assertEqual(prediction.transaction_id, "TX-API-ROUNDTRIP")
            self.assertFalse(prediction.prediction_is_fraud)
            self.assertEqual(prediction.fraud_probability, 0.05)
            self.assertEqual(prediction.model_name, "fdshield-fraud-detector-v2")
            self.assertEqual(prediction.model_version, "1")
            self.assertGreaterEqual(prediction.latency_ms, 0)

    def test_train1_single_digit_hours_are_normalized(self) -> None:
        row = valid_transaction_row("TX-API-SINGLE-HOUR")
        row["customer_registration_datetime"] = "2012-12-04 4:41"
        row["account_creation_datetime"] = "2017-11-13 0:52"
        row["transaction_datetime"] = "2025-01-01 1:02"

        parsed = TransactionCreateDTO.model_validate(row).raw_features

        self.assertEqual(parsed.customer_registration_datetime.hour, 4)
        self.assertEqual(parsed.account_creation_datetime.hour, 0)
        self.assertEqual(parsed.transaction_datetime.hour, 1)

    def test_missing_ml_feature_returns_422_without_partial_storage(self) -> None:
        row = valid_transaction_row("TX-API-MISSING-FEATURE")
        row.pop("location")

        response = self.client.post("/transactions", json=row)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("location", response.text)
        self.assertEqual(self.ml_client.calls, 0)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Transaction)).all(), [])
            self.assertEqual(session.exec(select(DerivedFeatures)).all(), [])

    def test_duplicate_transaction_id_returns_409(self) -> None:
        row = valid_transaction_row("TX-API-DUPLICATE")

        first = self.client.post("/transactions", json=row)
        duplicate = self.client.post("/transactions", json=row)

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(
            duplicate.json()["detail"],
            "이미 존재하는 transaction_id입니다.",
        )
        self.assertEqual(self.ml_client.calls, 1)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(Transaction)).all()), 1)

    def test_master_insert_race_retries_once_with_committed_winner(self) -> None:
        winner_row = valid_transaction_row("TX-API-RACE-WINNER")
        loser_row = valid_transaction_row("TX-API-RACE-LOSER")
        with Session(self.engine) as session:
            TransactionRepository(session).add_received(
                TransactionCreateDTO.model_validate(winner_row)
            )
            session.commit()

        original_add_received = TransactionRepository.add_received
        attempts = 0

        def add_received_after_customer_race(
            repository: TransactionRepository,
            payload: TransactionCreateDTO,
        ) -> Transaction:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                original = RuntimeError("duplicate customer primary key")
                original.diag = SimpleNamespace(constraint_name="customers_pkey")
                raise IntegrityError("INSERT customers", {}, original)
            return original_add_received(repository, payload)

        with patch.object(
            TransactionRepository,
            "add_received",
            new=add_received_after_customer_race,
        ):
            response = self.client.post("/transactions", json=loser_row)

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(attempts, 2)
        self.assertEqual(self.ml_client.calls, 1)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(Customer)).all()), 1)
            self.assertEqual(len(session.exec(select(Transaction)).all()), 2)
            self.assertIsNotNone(session.get(Transaction, "TX-API-RACE-LOSER"))

    def test_ml_failure_keeps_transaction_and_derived_snapshot(self) -> None:
        app.dependency_overrides[get_ml_serving_client] = lambda: FailingMLClient()
        row = valid_transaction_row("TX-API-ML-FAILED")
        expected = TransactionCreateDTO.model_validate(row).raw_features.model_dump(
            mode="json",
            by_alias=True,
        )

        created = self.client.post("/transactions", json=row)

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["prediction_status"], "FAILED")
        self.assertEqual(created.json()["raw_features"], expected)
        self.assertIsNone(created.json()["fraud_probability"])

        detail = self.client.get("/transactions/TX-API-ML-FAILED")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["prediction_status"], "NOT_AVAILABLE")
        self.assertEqual(detail.json()["raw_features"], expected)

        with Session(self.engine) as session:
            transaction = session.get(Transaction, "TX-API-ML-FAILED")
            derived = session.get(DerivedFeatures, "TX-API-ML-FAILED")
            self.assertIsNotNone(transaction)
            self.assertIsNotNone(derived)
            assert transaction is not None
            assembled = TransactionRepository(session).load_ml_features(transaction)
            self.assertIsNotNone(assembled)
            assert assembled is not None
            self.assertEqual(
                assembled.model_dump(mode="json", by_alias=True),
                expected,
            )
            self.assertEqual(session.exec(select(MLPredictionResult)).all(), [])
            self.assertEqual(session.exec(select(FraudTypeScoreResult)).all(), [])

    def test_fraud_prediction_with_active_rules_saves_all_four_scores(self) -> None:
        rule_set_id = self._activate_default_rules()
        fraud_client = SuccessfulNormalMLClient(
            is_fraud=True,
            fraud_probability=0.75,
        )
        app.dependency_overrides[get_ml_serving_client] = lambda: fraud_client

        response = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX-API-RULE-SCORED"),
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["ml_is_fraud"])
        self.assertEqual(body["rule_set_id"], rule_set_id)
        self.assertEqual(
            set(body["rule_scores"]),
            {
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
            },
        )
        with Session(self.engine) as session:
            score = session.exec(select(FraudTypeScoreResult)).one()
            self.assertEqual(score.transaction_id, "TX-API-RULE-SCORED")
            self.assertEqual(score.rule_set_id, rule_set_id)
            self.assertEqual(set(score.type_scores), set(body["rule_scores"]))
            self.assertEqual(set(score.matched_components), set(body["rule_scores"]))
            # 룰이 실제로 돌았으므로 APPLIED. 이 컬럼은 더 이상 nullable이 아니다.
            self.assertEqual(score.rule_filter_status, "APPLIED")
            self.assertIsNone(score.primary_fraud_type)

    def test_normal_prediction_skips_rule_scores_even_with_active_rules(self) -> None:
        self._activate_default_rules()

        response = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX-API-RULE-SKIPPED"),
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["ml_is_fraud"])
        self.assertIsNone(response.json()["rule_scores"])
        self.assertIsNone(response.json()["rule_set_id"])
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(FraudTypeScoreResult)).all(), [])

    def test_label_create_repeat_and_change_preserve_expected_timestamp(self) -> None:
        transaction_id = "TX-API-LABEL"
        created = self.client.post(
            "/transactions",
            json=valid_transaction_row(transaction_id),
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertIsNone(created.json()["confirmed_is_fraud"])

        first = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": True},
        )
        repeated = self.client.put(
            f"/transactions/{transaction_id}/label",
            json={"confirmed_is_fraud": True},
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(first.json()["confirmed_is_fraud"])
        self.assertEqual(repeated.json()["labeled_at"], first.json()["labeled_at"])

        changed_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
        with patch("app.repositories.transaction.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = changed_at
            changed = self.client.put(
                f"/transactions/{transaction_id}/label",
                json={"confirmed_is_fraud": False},
            )

        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["confirmed_is_fraud"])
        self.assertNotEqual(changed.json()["labeled_at"], first.json()["labeled_at"])
        detail = self.client.get(f"/transactions/{transaction_id}")
        self.assertFalse(detail.json()["confirmed_is_fraud"])
        self.assertEqual(detail.json()["labeled_at"], changed.json()["labeled_at"])

    def test_different_customers_can_share_personal_identifier(self) -> None:
        shared_name = "김민수"
        first_row = {
            **valid_transaction_row("TX-API-SAME-NAME-1"),
            "customer_id": "C-SAME-NAME-1",
            "customer_name": shared_name,
            "customer_identification_number": "identity-same-name-1",
            "account_account_number": "account-same-name-1",
            "recipient_account_number": "recipient-same-name-1",
        }
        second_row = {
            **valid_transaction_row("TX-API-SAME-NAME-2"),
            "customer_id": "C-SAME-NAME-2",
            "customer_name": shared_name,
            "customer_identification_number": "identity-same-name-2",
            "account_account_number": "account-same-name-2",
            "recipient_account_number": "recipient-same-name-2",
        }

        first = self.client.post("/transactions", json=first_row)
        second = self.client.post("/transactions", json=second_row)

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        with Session(self.engine) as session:
            customers = session.exec(select(Customer)).all()
            self.assertEqual(len(customers), 2)
            self.assertEqual(
                {customer.personal_identifier for customer in customers},
                {shared_name},
            )

    def test_master_profile_mismatches_update_latest_values(self) -> None:
        first = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX-API-MASTER-1"),
        )
        self.assertEqual(first.status_code, 201, first.text)

        customer_update = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-API-MASTER-2"),
                "customer_credit_rating": 5,
            },
        )
        self.assertEqual(customer_update.status_code, 201, customer_update.text)

        account_update = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-API-MASTER-3"),
                "customer_credit_rating": 5,
                "account_amount_daily_limit": 20_000_000,
            },
        )
        self.assertEqual(account_update.status_code, 201, account_update.text)

        with Session(self.engine) as session:
            customer = session.get(Customer, "C000494")
            account = session.get(Account, "123456789400")
            self.assertIsNotNone(customer)
            self.assertIsNotNone(account)
            self.assertEqual(customer.credit_rating, 5)
            self.assertEqual(account.amount_daily_limit, 20_000_000)

    def test_recipient_claim_and_other_owner_conflict(self) -> None:
        target_account = "recipient-becomes-source"
        first = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-API-RECIPIENT"),
                "recipient_account_number": target_account,
                "recipient_account_suspend_status": True,
            },
        )
        self.assertEqual(first.status_code, 201, first.text)

        claimed = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-API-CLAIM"),
                "customer_id": "C-CLAIM",
                "customer_name": "실소유자",
                "customer_identification_number": "identity-claim",
                "account_account_number": target_account,
                "recipient_account_number": "claim-recipient",
            },
        )
        self.assertEqual(claimed.status_code, 201, claimed.text)
        with Session(self.engine) as session:
            account = session.get(Account, target_account)
            self.assertIsNotNone(account)
            self.assertEqual(account.customer_id, "C-CLAIM")
            self.assertIsNotNone(account.account_type)

        wrong_owner = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-API-WRONG-OWNER"),
                "customer_id": "C-WRONG",
                "customer_name": "다른고객",
                "customer_identification_number": "identity-wrong",
                "account_account_number": target_account,
                "recipient_account_number": "wrong-recipient",
            },
        )
        self.assertEqual(wrong_owner.status_code, 409)
        self.assertEqual(
            wrong_owner.json()["detail"],
            "이미 다른 고객이 소유한 출금 계좌입니다.",
        )

    def test_identification_number_conflict_keeps_existing_response(self) -> None:
        first = self.client.post(
            "/transactions",
            json=valid_transaction_row("TX-IDENTIFICATION-1"),
        )
        self.assertEqual(first.status_code, 201, first.text)

        conflict = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-IDENTIFICATION-2"),
                "customer_id": "C-IDENTIFICATION-2",
                "customer_name": "다른고객",
                "account_account_number": "identification-source-2",
                "recipient_account_number": "identification-recipient-2",
            },
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["detail"],
            "이미 다른 고객에 사용 중인 identification_number입니다.",
        )

    def test_invalid_ip_and_mac_return_422(self) -> None:
        invalid_ip = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-INVALID-IP"),
                "ip_address": "999.1.1.1",
            },
        )
        self.assertEqual(invalid_ip.status_code, 422)
        self.assertIn("ip_address", invalid_ip.text)

        invalid_mac = self.client.post(
            "/transactions",
            json={
                **valid_transaction_row("TX-INVALID-MAC"),
                "mac_address": "not-a-mac",
            },
        )
        self.assertEqual(invalid_mac.status_code, 422)
        self.assertIn("mac_address", invalid_mac.text)


if __name__ == "__main__":
    unittest.main()
