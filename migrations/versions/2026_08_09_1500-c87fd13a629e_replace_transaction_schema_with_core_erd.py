"""replace the prototype transaction schema with the core ERD

Revision ID: c87fd13a629e
Revises: 5d2b8c1f4a90
Create Date: 2026-08-09 15:00:00.000000

This is intentionally destructive for the disposable development database. The
prototype rows cannot be reliably split into customer, account, and transaction
entities, so transaction/result data is cleared when this revision is applied.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c87fd13a629e"
down_revision: Union[str, Sequence[str], None] = "5d2b8c1f4a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    """Discard prototype transaction data and install the agreed core ERD."""

    op.drop_table("fraud_type_score_results")
    op.drop_table("ml_prediction_results")
    op.drop_table("transactions")

    op.create_table(
        "customers",
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("personal_identifier", sa.String(length=255), nullable=False),
        sa.Column("identification_number", sa.String(length=255), nullable=False),
        sa.Column("registration_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credit_rating", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("customer_id"),
        sa.UniqueConstraint("personal_identifier", name="uq_customers_personal_identifier"),
        sa.UniqueConstraint("identification_number", name="uq_customers_identification_number"),
    )
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("account_number", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=True),
        sa.Column("creation_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("account_id"),
        sa.UniqueConstraint("account_number", name="uq_accounts_account_number"),
    )
    op.create_index("ix_accounts_customer_id", "accounts", ["customer_id"])

    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("source_account_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_account_id", sa.String(length=64), nullable=True),
        sa.Column("transaction_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_amount", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("raw_features", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_account_id"], ["accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_account_id"], ["accounts.account_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    for column_name in ("customer_id", "source_account_id", "recipient_account_id"):
        op.create_index(
            f"ix_transactions_{column_name}", "transactions", [column_name]
        )

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("gcs_uri", sa.String(length=2048), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_dataset_versions_version"),
    )
    op.create_table(
        "training_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("model_key", sa.String(length=128), nullable=False),
        sa.Column("dataset_version_id", sa.BigInteger(), nullable=False),
        sa.Column("cloud_run_execution_name", sa.String(length=512), nullable=False),
        sa.Column("mlflow_run_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_training_runs_dataset_version_id", "training_runs", ["dataset_version_id"]
    )
    op.create_index("ix_training_runs_status", "training_runs", ["status"])

    op.create_table(
        "ml_prediction_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("prediction_is_fraud", sa.Boolean(), nullable=False),
        sa.Column("fraud_probability", sa.Float(), nullable=False),
        sa.Column("shap", _jsonb(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ml_prediction_results_transaction_id",
        "ml_prediction_results",
        ["transaction_id"],
    )
    op.create_index(
        "ix_ml_prediction_results_created_at",
        "ml_prediction_results",
        ["created_at"],
    )

    op.create_table(
        "transaction_labels",
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("confirmed_is_fraud", sa.Boolean(), nullable=False),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )

    op.create_table(
        "fraud_type_score_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.Column("type_scores", _jsonb(), nullable=False),
        sa.Column("matched_components", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], ["fraud_rule_sets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_id", name="uq_fraud_type_score_results_transaction_id"
        ),
    )
    for column_name in ("transaction_id", "rule_set_id", "created_at"):
        op.create_index(
            f"ix_fraud_type_score_results_{column_name}",
            "fraud_type_score_results",
            [column_name],
        )


def downgrade() -> None:
    """Restore the schema produced by the preceding prototype revision."""

    op.drop_table("fraud_type_score_results")
    op.drop_table("transaction_labels")
    op.drop_table("ml_prediction_results")
    op.drop_table("training_runs")
    op.drop_table("dataset_versions")
    op.drop_table("transactions")
    op.drop_table("accounts")
    op.drop_table("customers")

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_method", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_data", _jsonb(), nullable=False),
        sa.Column("prediction_status", sa.String(length=16), nullable=False),
        sa.Column("ml_is_fraud", sa.Boolean(), nullable=True),
        sa.Column("fraud_probability", sa.Float(), nullable=True),
        sa.Column("shap", _jsonb(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transactions_transaction_id", "transactions", ["transaction_id"], unique=True
    )
    op.create_index(
        "ix_transactions_prediction_status", "transactions", ["prediction_status"]
    )

    op.create_table(
        "ml_prediction_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("prediction_is_fraud", sa.Boolean(), nullable=False),
        sa.Column("fraud_probability", sa.Float(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ml_prediction_results_transaction_id",
        "ml_prediction_results",
        ["transaction_id"],
    )
    op.create_index(
        "ix_ml_prediction_results_created_at",
        "ml_prediction_results",
        ["created_at"],
    )

    op.create_table(
        "fraud_type_score_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("type_scores", _jsonb(), nullable=False),
        sa.Column("matched_components", _jsonb(), nullable=False),
        sa.Column("rule_set_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"], ["fraud_rule_sets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_id", name="uq_fraud_type_score_results_transaction_id"
        ),
    )
    for column_name in (
        "transaction_id",
        "rule_set_version",
        "created_at",
        "rule_set_id",
    ):
        op.create_index(
            f"ix_fraud_type_score_results_{column_name}",
            "fraud_type_score_results",
            [column_name],
        )
