import os
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.api.fraud_rule import router as fraud_rule_router
from app.core.db import get_session
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.dto.fraud_rule import (
    FraudRuleReplayChangedTransactionResponse,
    FraudRuleReplayComponentImpactResponse,
)
from app.dto.ml_features import MLTransactionFeatures
from app.services.features.ml_feature_assembler import (
    build_account_fields,
    build_customer_fields,
    build_derived_features_fields,
    build_transaction_fields,
)
from app.services.rules.replay import _component_changes, replay_rule_sets
from tests.test_rule_feature_builder import valid_rule_raw_data

ADMIN_HEADERS = {"X-MLOps-Admin-Token": "admin-secret"}

app = FastAPI()
app.include_router(fraud_rule_router)


_TRANSACTION_IDS: dict[str, int] = {}


def _transaction_id(value: str | int) -> int:
    if isinstance(value, int):
        return value
    return _TRANSACTION_IDS.setdefault(value, len(_TRANSACTION_IDS) + 1)


def _transaction_features(
    transaction_id: str,
    *,
    transaction_datetime: datetime,
    loan_type: str = "c",
) -> MLTransactionFeatures:
    suffix = transaction_id.lower()
    raw_features = valid_rule_raw_data()
    raw_features.update(
        {
            "customer_name": "동명이인 허용 고객",
            "account_account_number": f"source-{suffix}",
            "recipient_account_number": f"recipient-{suffix}",
            "customer_loan_type": loan_type,
            "transaction_datetime": transaction_datetime.isoformat(),
        }
    )
    return MLTransactionFeatures.model_validate(raw_features)


def _save_transaction(
    session: Session,
    transaction_id: str,
    *,
    transaction_datetime: datetime,
    loan_type: str = "c",
) -> Transaction:
    features = _transaction_features(
        transaction_id,
        transaction_datetime=transaction_datetime,
        loan_type=loan_type,
    )
    suffix = transaction_id.lower()
    customer = Customer(
        id=f"C-{suffix}",
        name=features.customer_name,
        identification_number=f"identity-{suffix}",
        **build_customer_fields(features),
    )
    source = Account(
        id=f"source-{suffix}",
        customer_id=customer.id,
        account_number=str(features.account_account_number),
        **build_account_fields(features),
    )
    recipient = Account(
        id=f"recipient-{suffix}",
        account_number=str(features.recipient_account_number),
    )
    session.add(customer)
    session.flush()
    session.add(source)
    session.add(recipient)
    session.flush()
    transaction = Transaction(
        id=_transaction_id(transaction_id),
        customer_id=customer.id,
        source_account_number=source.account_number,
        recipient_account_number=recipient.account_number,
        ip_address=features.ip_address,
        mac_address=features.mac_address,
        **build_transaction_fields(features),
    )
    session.add(transaction)
    session.flush()
    session.add(
        DerivedFeatures(
            id=transaction.id,
            **build_derived_features_fields(features),
        )
    )
    session.commit()
    session.refresh(transaction)
    return transaction


def _prediction(
    transaction_id: str | int,
    *,
    is_fraud: bool,
    created_at: datetime,
) -> MLPredictionResult:
    return MLPredictionResult(
        transaction_id=_transaction_id(transaction_id),
        predict_result=is_fraud,
        predict_proba=0.9 if is_fraud else 0.1,
        model_name="fdshield-fraud-detector",
        model_version="5",
        latency_ms=10,
        created_at=created_at,
    )


class FraudRuleReplayApiTest(unittest.TestCase):
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

    def _active_and_draft(self) -> tuple[dict[str, object], dict[str, object]]:
        first = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        activated = self.client.post(
            f"/rule-sets/{first['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        return activated.json(), draft

    def _add_always_matching_type(self, draft_id: int) -> None:
        response = self.client.post(
            f"/rule-sets/{draft_id}/rules",
            headers=ADMIN_HEADERS,
            json={
                "type_code": "CUSTOM_FRAUD",
                "display_name": "테스트 유형",
                "components": [
                    {
                        "component_key": "positive_amount",
                        "name": "양수 거래금액",
                        "condition_expression": {
                            "field": "transaction_amount",
                            "operator": "GT",
                            "value": 0,
                        },
                        "weight": 1.0,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

    @staticmethod
    def _stored_score_snapshot(
        session: Session,
    ) -> list[tuple[object, ...]]:
        rows = session.exec(
            select(FraudTypeScoreResult).order_by(FraudTypeScoreResult.id)
        ).all()
        return [
            (
                item.id,
                item.transaction_id,
                item.rule_set_id,
                deepcopy(item.type_scores),
                deepcopy(item.matched_components),
                item.created_at,
            )
            for item in rows
        ]

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_replay_bulk_assembles_latest_positive_sample_without_writes(
        self,
    ) -> None:
        active, draft = self._active_and_draft()
        self._add_always_matching_type(int(draft["id"]))

        base = datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)
        with Session(self.engine) as session:
            for transaction_id, offset in (
                ("TX-LATEST-NEGATIVE", 5),
                ("TX-LATEST-POSITIVE", 4),
                ("TX-POSITIVE", 3),
                ("TX-OLDER-POSITIVE", 2),
                ("TX-NO-PREDICTION", 1),
            ):
                _save_transaction(
                    session,
                    transaction_id,
                    transaction_datetime=base + timedelta(hours=offset),
                )

            same_prediction_time = base + timedelta(hours=6)
            session.add_all(
                [
                    _prediction(
                        "TX-LATEST-NEGATIVE",
                        is_fraud=True,
                        created_at=same_prediction_time,
                    ),
                    _prediction(
                        "TX-LATEST-NEGATIVE",
                        is_fraud=False,
                        created_at=same_prediction_time,
                    ),
                    _prediction(
                        "TX-LATEST-POSITIVE",
                        is_fraud=False,
                        created_at=same_prediction_time,
                    ),
                    _prediction(
                        "TX-LATEST-POSITIVE",
                        is_fraud=True,
                        created_at=same_prediction_time,
                    ),
                    _prediction(
                        "TX-POSITIVE",
                        is_fraud=True,
                        created_at=base + timedelta(hours=7),
                    ),
                    _prediction(
                        "TX-OLDER-POSITIVE",
                        is_fraud=True,
                        created_at=base + timedelta(hours=8),
                    ),
                ]
            )
            session.commit()
            session.add(
                FraudTypeScoreResult(
                    transaction_id=_transaction_id("TX-LATEST-POSITIVE"),
                    rule_set_id=int(active["id"]),
                    rule_filter_status="APPLIED",
                    type_scores={"SENTINEL": 0.123},
                    matched_components={"SENTINEL": ["unchanged"]},
                )
            )
            session.commit()
            before = self._stored_score_snapshot(session)

        write_statements: list[str] = []
        feature_queries: list[str] = []

        def capture_statements(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = statement.lstrip().upper()
            if normalized.startswith(("INSERT", "UPDATE", "DELETE")):
                write_statements.append(statement)
            if normalized.startswith("SELECT") and "derived_features" in statement:
                feature_queries.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statements)
        try:
            response = self.client.post(
                f"/rule-sets/{draft['id']}/replay",
                headers=ADMIN_HEADERS,
                json={"sample_size": 2, "detail_limit": 1},
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statements)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["selection_basis"], "LATEST_ML_POSITIVE")
        self.assertEqual(body["aggregation_basis"], "EVALUATED_ONLY")
        self.assertEqual(body["selected_count"], 2)
        self.assertEqual(body["evaluated_count"], 2)
        self.assertEqual(body["error_count"], 0)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["changed_transaction_count"], 2)
        self.assertEqual(body["score_changed_transaction_count"], 2)
        self.assertEqual(body["evidence_changed_transaction_count"], 2)
        self.assertEqual(
            [item["transaction_id"] for item in body["changed_transaction_details"]],
            [_transaction_id("TX-LATEST-POSITIVE")],
        )
        self.assertTrue(body["changed_details_truncated"])
        self.assertEqual(write_statements, [])
        self.assertEqual(len(feature_queries), 1)

        impact = next(
            item
            for item in body["component_impacts"]
            if item["type_code"] == "CUSTOM_FRAUD"
        )
        self.assertEqual(impact["newly_matched_transaction_count"], 2)
        self.assertEqual(impact["no_longer_matched_transaction_count"], 0)
        with Session(self.engine) as session:
            self.assertEqual(self._stored_score_snapshot(session), before)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_component_impact_reports_churn_when_net_count_is_zero(self) -> None:
        first_draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        added = self.client.post(
            f"/rule-sets/{first_draft['id']}/rules",
            headers=ADMIN_HEADERS,
            json={
                "type_code": "LOAN_TEST",
                "display_name": "매칭 대상 변경 테스트",
                "components": [
                    {
                        "component_key": "target_loan_context",
                        "name": "대출 관련 거래",
                        "condition_expression": {
                            "field": "loan_related",
                            "operator": "EQ",
                            "value": True,
                        },
                        "weight": 1.0,
                    }
                ],
            },
        )
        self.assertEqual(added.status_code, 201, added.text)
        activated = self.client.post(
            f"/rule-sets/{first_draft['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        draft = self.client.post("/rule-sets/drafts", headers=ADMIN_HEADERS).json()
        draft_rule = next(
            rule for rule in draft["rules"] if rule["type_code"] == "LOAN_TEST"
        )
        updated = self.client.put(
            f"/rule-sets/{draft['id']}/rules/{draft_rule['id']}",
            headers=ADMIN_HEADERS,
            json={
                "components": [
                    {
                        "component_key": "target_loan_context",
                        "name": "비대출 거래",
                        "condition_expression": {
                            "field": "loan_related",
                            "operator": "EQ",
                            "value": False,
                        },
                        "weight": 1.0,
                    }
                ]
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        base = datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)
        with Session(self.engine) as session:
            loan = _save_transaction(
                session,
                "TX-LOAN",
                transaction_datetime=base,
                loan_type="c",
            )
            no_loan = _save_transaction(
                session,
                "TX-NO-LOAN",
                transaction_datetime=base + timedelta(minutes=1),
                loan_type="a",
            )
            session.add_all(
                [
                    _prediction(
                        transaction.id,
                        is_fraud=True,
                        created_at=base + timedelta(hours=1, seconds=index),
                    )
                    for index, transaction in enumerate((loan, no_loan))
                ]
            )
            session.commit()

        response = self.client.post(
            f"/rule-sets/{draft['id']}/replay",
            headers=ADMIN_HEADERS,
            json={"sample_size": 2},
        )

        self.assertEqual(response.status_code, 200, response.text)
        impact = next(
            item
            for item in response.json()["component_impacts"]
            if item["type_code"] == "LOAN_TEST"
        )
        self.assertEqual(impact["active_matched_transaction_count"], 1)
        self.assertEqual(impact["draft_matched_transaction_count"], 1)
        self.assertEqual(impact["matched_transaction_count_delta"], 0)
        self.assertEqual(impact["newly_matched_transaction_count"], 1)
        self.assertEqual(impact["no_longer_matched_transaction_count"], 1)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_missing_normalized_feature_row_is_not_selected(self) -> None:
        _, draft = self._active_and_draft()
        base = datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)
        with Session(self.engine) as session:
            transaction = _save_transaction(
                session,
                "TX-MISSING-DERIVED",
                transaction_datetime=base,
            )
            derived = session.get(DerivedFeatures, transaction.id)
            session.delete(derived)
            session.add(
                _prediction(
                    transaction.id,
                    is_fraud=True,
                    created_at=base + timedelta(minutes=1),
                )
            )
            session.commit()

        response = self.client.post(
            f"/rule-sets/{draft['id']}/replay",
            headers=ADMIN_HEADERS,
            json={"sample_size": 1},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["selected_count"], 0)
        self.assertEqual(body["evaluated_count"], 0)
        self.assertEqual(body["error_count"], 0)
        self.assertEqual(body["summary_denominator"], 0)
        self.assertIsNone(body["changed_transaction_rate"])
        self.assertEqual(body["error_details"], [])
        self.assertTrue(
            all(
                summary["active_average_score"] is None
                and summary["draft_average_score"] is None
                and summary["average_score_delta"] is None
                for summary in body["type_summaries"]
            )
        )

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_replay_defaults_bounds_and_required_states(self) -> None:
        draft_without_active = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        no_active = self.client.post(
            f"/rule-sets/{draft_without_active['id']}/replay",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(no_active.status_code, 409, no_active.text)

        activated = self.client.post(
            f"/rule-sets/{draft_without_active['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        active_target = self.client.post(
            f"/rule-sets/{draft_without_active['id']}/replay",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(active_target.status_code, 409, active_target.text)

        draft = self.client.post("/rule-sets/drafts", headers=ADMIN_HEADERS).json()
        default_response = self.client.post(
            f"/rule-sets/{draft['id']}/replay",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(default_response.status_code, 200, default_response.text)
        self.assertEqual(default_response.json()["requested_count"], 1000)
        self.assertEqual(default_response.json()["detail_limit"], 100)

        for invalid_sample_size in (0, 1001, True):
            response = self.client.post(
                f"/rule-sets/{draft['id']}/replay",
                headers=ADMIN_HEADERS,
                json={"sample_size": invalid_sample_size},
            )
            self.assertEqual(response.status_code, 422, response.text)
        for invalid_detail_limit in (-1, 101, True):
            response = self.client.post(
                f"/rule-sets/{draft['id']}/replay",
                headers=ADMIN_HEADERS,
                json={"detail_limit": invalid_detail_limit},
            )
            self.assertEqual(response.status_code, 422, response.text)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_replay_rejects_result_if_rule_set_changes_during_execution(
        self,
    ) -> None:
        _, draft = self._active_and_draft()

        def mutate_draft_after_replay(**kwargs):
            result = replay_rule_sets(**kwargs)
            session = kwargs["session"]
            current = session.get(FraudRuleSet, int(draft["id"]))
            current.updated_at = current.updated_at + timedelta(seconds=1)
            session.add(current)
            session.commit()
            return result

        with patch(
            "app.api.fraud_rule.replay_rule_sets",
            side_effect=mutate_draft_after_replay,
        ):
            response = self.client.post(
                f"/rule-sets/{draft['id']}/replay",
                headers=ADMIN_HEADERS,
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("도중 룰셋이 변경", response.json()["detail"])


class FraudRuleReplayInvariantTest(unittest.TestCase):
    def test_component_order_and_empty_type_keys_are_not_evidence_changes(self) -> None:
        added, removed = _component_changes(
            {"VOICE_PHISHING": ["second", "first"], "EMPTY": []},
            {"VOICE_PHISHING": ["first", "second"], "EMPTY": []},
        )
        self.assertEqual(added, {})
        self.assertEqual(removed, {})

    def test_changed_detail_rejects_empty_evidence_keys(self) -> None:
        with self.assertRaises(ValidationError):
            FraudRuleReplayChangedTransactionResponse(
                transaction_id=999,
                transaction_datetime=datetime.now(UTC),
                score_changed=True,
                evidence_changed=True,
                max_absolute_score_delta=0.1,
                active_type_scores={"TYPE": 0.1},
                draft_type_scores={"TYPE": 0.2},
                score_deltas={"TYPE": 0.1},
                added_matched_components={"TYPE": []},
                removed_matched_components={},
            )

    def test_component_impact_rejects_inconsistent_churn_counts(self) -> None:
        with self.assertRaises(ValidationError):
            FraudRuleReplayComponentImpactResponse(
                type_code="TYPE",
                component_key="component",
                display_name="구성요소",
                active_present=True,
                draft_present=True,
                active_weight=1.0,
                draft_weight=1.0,
                definition_changed=True,
                active_matched_transaction_count=1,
                draft_matched_transaction_count=1,
                matched_transaction_count_delta=0,
                newly_matched_transaction_count=1,
                no_longer_matched_transaction_count=0,
            )


if __name__ == "__main__":
    unittest.main()
