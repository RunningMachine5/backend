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
    FraudRuleSetStatus,
)
from app.services.rules.feature_builder import RULE_CONTEXT_FIELDS
from main import app
from tests.test_rule_feature_builder import valid_rule_raw_data

ADMIN_HEADERS = {"X-MLOps-Admin-Token": "admin-secret"}


class FraudRuleApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        FraudRuleSet.__table__.create(self.engine)
        FraudRule.__table__.create(self.engine)
        FraudRuleComponent.__table__.create(self.engine)

        def override_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_management_api_requires_existing_admin_token(self) -> None:
        response = self.client.get("/rule-sets")

        self.assertEqual(response.status_code, 401)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_first_draft_bootstraps_four_default_rules_and_validates(self) -> None:
        response = self.client.post("/rule-sets/drafts", headers=ADMIN_HEADERS)

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["status"], "DRAFT")
        self.assertEqual(len(body["rules"]), 4)
        self.assertEqual(
            {rule["type_code"] for rule in body["rules"]},
            {
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
            },
        )

        validation = self.client.post(
            f"/rule-sets/{body['id']}/validate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(validation.status_code, 200, validation.text)
        self.assertTrue(validation.json()["valid"])

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_feature_registry_uses_canonical_account_release_name(self) -> None:
        response = self.client.get("/rule-features", headers=ADMIN_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        fields = {feature["field"] for feature in response.json()}
        self.assertEqual(fields, set(RULE_CONTEXT_FIELDS))
        self.assertIn("recipient_release_suspension", fields)
        self.assertNotIn("account_release_suspension", fields)
        self.assertNotIn("customer_name", fields)
        self.assertNotIn("account_account_number", fields)
        self.assertNotIn("recipient_account_number", fields)
        self.assertNotIn("ip_address", fields)
        self.assertNotIn("mac_address", fields)
        self.assertNotIn("location", fields)
        self.assertNotIn("customer_birth_date", fields)
        self.assertIn("transaction_age", fields)
        self.assertIn("strong_auth_change", fields)
        self.assertIn("all_limit_actions", fields)
        self.assertIn("severe_amount_context", fields)
        self.assertIn("amount_anomaly", fields)
        self.assertIn("impossible_travel", fields)
        self.assertNotIn("card_context_proxy", fields)
        account_type = next(
            feature
            for feature in response.json()
            if feature["field"] == "account_account_type"
        )
        self.assertEqual(account_type["allowed_values"], ["a", "b", "c", "d", "e"])

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_rule_set_test_returns_all_default_rule_scores(self) -> None:
        draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()

        raw_data = valid_rule_raw_data()
        raw_data.update(
            {
                "customer_flag_terminal_malicious_behavior_1": 1,
                "customer_flag_terminal_malicious_behavior_2": 1,
                "customer_loan_type": "b",
                "customer_inquery_atm_limit": 1,
                "customer_increase_atm_limit": 1,
                "account_indicator_release_limit_excess": 1,
                "transaction_amount": 9_000_000,
                "transaction_history_with_the_account": 1,
                "another_person_account": 1,
            }
        )
        response = self.client.post(
            f"/rule-sets/{draft['id']}/test",
            headers=ADMIN_HEADERS,
            json={"raw_data": raw_data},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["type_scores"]), 4)
        score_by_type = {
            item["type_code"]: item["score"] for item in body["type_scores"]
        }
        self.assertAlmostEqual(score_by_type["VOICE_PHISHING"], 1.0)
        self.assertNotIn("status", body)
        self.assertNotIn("fraud_type", body)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_new_draft_cannot_activate_transition_only_legacy_field(self) -> None:
        draft = self.client.post("/rule-sets/drafts", headers=ADMIN_HEADERS).json()
        added = self.client.post(
            f"/rule-sets/{draft['id']}/rules",
            headers=ADMIN_HEADERS,
            json={
                "type_code": "LEGACY_BYPASS",
                "display_name": "레거시 우회",
                "components": [
                    {
                        "component_key": "legacy_open_banking",
                        "name": "레거시 오픈뱅킹",
                        "condition_expression": {
                            "field": "Account_indicator_Openbanking",
                            "operator": "EQ",
                            "value": 1,
                        },
                        "weight": 1.0,
                    }
                ],
            },
        )
        self.assertEqual(added.status_code, 201, added.text)

        validation = self.client.post(
            f"/rule-sets/{draft['id']}/validate",
            headers=ADMIN_HEADERS,
        )

        self.assertEqual(validation.status_code, 200, validation.text)
        self.assertFalse(validation.json()["valid"])
        self.assertIn("raw51 snake_case", validation.text)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_activation_archives_previous_set_and_clone_is_editable(self) -> None:
        first = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        activated = self.client.post(
            f"/rule-sets/{first['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "ACTIVE")

        immutable = self.client.put(
            f"/rule-sets/{first['id']}/rules/{first['rules'][0]['id']}",
            headers=ADMIN_HEADERS,
            json={"display_name": "수정 불가"},
        )
        self.assertEqual(immutable.status_code, 409)

        second = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(second.status_code, 201, second.text)
        second_body = second.json()
        self.assertEqual(second_body["version"], 2)
        self.assertEqual(len(second_body["rules"]), 4)

        added = self.client.post(
            f"/rule-sets/{second_body['id']}/rules",
            headers=ADMIN_HEADERS,
            json={
                "type_code": "CUSTOM_FRAUD",
                "display_name": "신규 사기유형",
                "components": [
                    {
                        "component_key": "loan_related_only",
                        "name": "대출 관련 여부",
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

        activated_second = self.client.post(
            f"/rule-sets/{second_body['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated_second.status_code, 200, activated_second.text)
        self.assertEqual(activated_second.json()["status"], "ACTIVE")

        with Session(self.engine) as session:
            stored_first = session.get(FraudRuleSet, first["id"])
            stored_second = session.get(FraudRuleSet, second_body["id"])
            self.assertEqual(stored_first.status, FraudRuleSetStatus.ARCHIVED)
            self.assertEqual(stored_second.status, FraudRuleSetStatus.ACTIVE)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_invalid_weight_sum_cannot_be_activated(self) -> None:
        draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        loan_rule = next(
            rule for rule in draft["rules"] if rule["type_code"] == "VOICE_PHISHING"
        )
        components = loan_rule["components"]
        components[0]["weight"] = 0.50
        replacement = [
            {
                "component_key": component["component_key"],
                "name": component["name"],
                "condition_expression": component["condition_expression"],
                "weight": component["weight"],
                "sort_order": component["sort_order"],
            }
            for component in components
        ]
        updated = self.client.put(
            f"/rule-sets/{draft['id']}/rules/{loan_rule['id']}",
            headers=ADMIN_HEADERS,
            json={"components": replacement},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        validation = self.client.post(
            f"/rule-sets/{draft['id']}/validate",
            headers=ADMIN_HEADERS,
        )
        self.assertFalse(validation.json()["valid"])
        self.assertIn("1.0", validation.text)

        activation = self.client.post(
            f"/rule-sets/{draft['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activation.status_code, 422)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_delete_rule_is_limited_to_draft(self) -> None:
        draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        rule_id = draft["rules"][0]["id"]

        deleted = self.client.delete(
            f"/rule-sets/{draft['id']}/rules/{rule_id}",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)

        with Session(self.engine) as session:
            self.assertIsNone(session.get(FraudRule, rule_id))
            remaining = session.exec(
                select(FraudRule).where(FraudRule.rule_set_id == draft["id"])
            ).all()
            self.assertEqual(len(remaining), 3)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_existing_draft_must_be_reused_or_discarded(self) -> None:
        first = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()

        duplicate = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        )

        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        body = duplicate.json()
        self.assertFalse(body["success"])
        self.assertIsNone(body["data"])
        self.assertEqual(body["error"]["code"], "HTTP_409")
        self.assertIn(str(first["id"]), body["error"]["message"])
        self.assertIsNone(body["error"]["details"])

        discarded = self.client.delete(
            f"/rule-sets/{first['id']}",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(discarded.status_code, 204, discarded.text)

        with Session(self.engine) as session:
            self.assertIsNone(session.get(FraudRuleSet, first["id"]))
            self.assertEqual(session.exec(select(FraudRule)).all(), [])
            self.assertEqual(session.exec(select(FraudRuleComponent)).all(), [])

        replacement = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(replacement.status_code, 201, replacement.text)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_active_rule_set_cannot_be_discarded(self) -> None:
        draft = self.client.post(
            "/rule-sets/drafts",
            headers=ADMIN_HEADERS,
        ).json()
        activated = self.client.post(
            f"/rule-sets/{draft['id']}/activate",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(activated.status_code, 200, activated.text)

        response = self.client.delete(
            f"/rule-sets/{draft['id']}",
            headers=ADMIN_HEADERS,
        )

        self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()
