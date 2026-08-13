import unittest

from sqlmodel import SQLModel

import app.data.model  # noqa: F401
from app.data.model.customer_event import CustomerEventType


class PR118SchemaModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tables = SQLModel.metadata.tables

    def test_primary_key_and_public_name_columns_match_final_contract(self) -> None:
        expected = {
            "customers": ["id"],
            "accounts": ["id"],
            "customer_events": ["id"],
            "transactions": ["id"],
            "derived_features": ["id"],
        }
        for table_name, primary_key in expected.items():
            with self.subTest(table=table_name):
                self.assertEqual(
                    list(self.tables[table_name].primary_key.columns.keys()),
                    primary_key,
                )

        self.assertIn("name", self.tables["customers"].c)
        self.assertNotIn("personal_identifier", self.tables["customers"].c)
        self.assertIn(
            "flag_deposit_more_than_tenMillion", self.tables["derived_features"].c
        )

    def test_account_number_and_customer_foreign_keys_match_final_contract(
        self,
    ) -> None:
        expected = {
            ("accounts", "customer_id"): "customers.id",
            ("customer_events", "customer_id"): "customers.id",
            ("customer_events", "account_number"): "accounts.account_number",
            ("transactions", "customer_id"): "customers.id",
            ("transactions", "source_account_number"): "accounts.account_number",
            ("transactions", "recipient_account_number"): "accounts.account_number",
            ("derived_features", "id"): "transactions.id",
        }
        for (table_name, column_name), target in expected.items():
            with self.subTest(table=table_name, column=column_name):
                self.assertEqual(
                    self._foreign_key_target(table_name, column_name), target
                )

    def test_child_transaction_columns_keep_name_but_reference_transactions_id(
        self,
    ) -> None:
        for table_name in (
            "transaction_labels",
            "ml_prediction_results",
            "fraud_type_score_results",
            "agent_cases",
            "agent_chat_sessions",
            "fraud_type_score_after_chat",
        ):
            with self.subTest(table=table_name):
                self.assertEqual(
                    self._foreign_key_target(table_name, "transaction_id"),
                    "transactions.id",
                )

    def test_nullable_and_length_changes_match_final_transaction_contract(self) -> None:
        transactions = self.tables["transactions"].c
        for column_name in (
            "access_medium",
            "initial_balance",
            "balance",
            "remaining_amount_daily_limit_exceeded",
            "operating_system",
        ):
            with self.subTest(column=column_name):
                self.assertTrue(transactions[column_name].nullable)
        self.assertEqual(transactions.error_code.type.length, 8)
        self.assertEqual(transactions.source_account_number.type.length, 255)
        self.assertEqual(transactions.recipient_account_number.type.length, 255)

    def test_customer_event_codes_match_ml_owned_values(self) -> None:
        self.assertEqual(
            {member.value for member in CustomerEventType},
            {
                "OFFICIAL_CERTIFICATION",
                "PRIVATE_CERTIFICATION",
                "SECURITY_CARD_OTP",
                "PRIVACY_MODIFICATION",
                "ATM_LIMIT_INQUIRY",
                "ATM_LIMIT_INCREASE",
                "SUSPENSION_START",
                "SUSPENSION_RELEASE",
            },
        )

    def _foreign_key_target(self, table_name: str, column_name: str) -> str:
        foreign_keys = self.tables[table_name].c[column_name].foreign_keys
        self.assertEqual(len(foreign_keys), 1)
        return next(iter(foreign_keys)).target_fullname


if __name__ == "__main__":
    unittest.main()
