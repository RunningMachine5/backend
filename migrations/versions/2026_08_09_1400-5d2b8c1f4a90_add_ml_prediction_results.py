"""add ML prediction results and link rule scores to rule sets

Revision ID: 5d2b8c1f4a90
Revises: a42d8f6c3e91
Create Date: 2026-08-09 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d2b8c1f4a90"
down_revision: Union[str, Sequence[str], None] = "a42d8f6c3e91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        unique=False,
    )
    op.create_index(
        "ix_ml_prediction_results_created_at",
        "ml_prediction_results",
        ["created_at"],
        unique=False,
    )

    op.add_column(
        "fraud_type_score_results",
        sa.Column("rule_set_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE fraud_type_score_results AS score_result "
            "SET rule_set_id = rule_set.id "
            "FROM fraud_rule_sets AS rule_set "
            "WHERE rule_set.version = score_result.rule_set_version"
        )
    )
    op.alter_column(
        "fraud_type_score_results",
        "rule_set_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_fraud_type_score_results_rule_set_id",
        "fraud_type_score_results",
        "fraud_rule_sets",
        ["rule_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_fraud_type_score_results_rule_set_id",
        "fraud_type_score_results",
        ["rule_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fraud_type_score_results_rule_set_id",
        table_name="fraud_type_score_results",
    )
    op.drop_constraint(
        "fk_fraud_type_score_results_rule_set_id",
        "fraud_type_score_results",
        type_="foreignkey",
    )
    op.drop_column("fraud_type_score_results", "rule_set_id")

    op.drop_index(
        "ix_ml_prediction_results_created_at",
        table_name="ml_prediction_results",
    )
    op.drop_index(
        "ix_ml_prediction_results_transaction_id",
        table_name="ml_prediction_results",
    )
    op.drop_table("ml_prediction_results")
