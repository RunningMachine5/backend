"""add fraud rule management and classification results

Revision ID: 7f1c9d4a2b6e
Revises: 9c72d841a6f4
Create Date: 2026-08-07 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "7f1c9d4a2b6e"
down_revision: Union[str, Sequence[str], None] = "9c72d841a6f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fraud_rule_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("minimum_score", sa.Float(), nullable=False),
        sa.Column("ambiguity_margin", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ambiguity_margin >= 0 AND ambiguity_margin <= 1",
            name="ck_fraud_rule_sets_ambiguity_margin",
        ),
        sa.CheckConstraint(
            "minimum_score >= 0 AND minimum_score <= 1",
            name="ck_fraud_rule_sets_minimum_score",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fraud_rule_sets_status"),
        "fraud_rule_sets",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_rule_sets_version"),
        "fraud_rule_sets",
        ["version"],
        unique=True,
    )

    op.create_table(
        "fraud_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.Column(
            "type_code",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "description",
            sqlmodel.sql.sqltypes.AutoString(length=1000),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_fraud_rules_sort_order",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["fraud_rule_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_set_id",
            "type_code",
            name="uq_fraud_rules_rule_set_type_code",
        ),
    )
    op.create_index(
        op.f("ix_fraud_rules_enabled"),
        "fraud_rules",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_rules_rule_set_id"),
        "fraud_rules",
        ["rule_set_id"],
        unique=False,
    )

    op.create_table(
        "fraud_rule_components",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column(
            "component_key",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "condition_expression",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_fraud_rule_components_sort_order",
        ),
        sa.CheckConstraint(
            "weight > 0 AND weight <= 1",
            name="ck_fraud_rule_components_weight",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["fraud_rules.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id",
            "component_key",
            name="uq_fraud_rule_components_rule_key",
        ),
    )
    op.create_index(
        op.f("ix_fraud_rule_components_rule_id"),
        "fraud_rule_components",
        ["rule_id"],
        unique=False,
    )

    op.create_table(
        "fraud_type_classification_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "transaction_id",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "fraud_type",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("second_score", sa.Float(), nullable=True),
        sa.Column("score_gap", sa.Float(), nullable=True),
        sa.Column(
            "type_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "matched_components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("rule_set_version", sa.Integer(), nullable=True),
        sa.Column(
            "error_message",
            sqlmodel.sql.sqltypes.AutoString(length=1000),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "score_gap IS NULL OR (score_gap >= 0 AND score_gap <= 1)",
            name="ck_fraud_type_results_score_gap",
        ),
        sa.CheckConstraint(
            "second_score IS NULL OR (second_score >= 0 AND second_score <= 1)",
            name="ck_fraud_type_results_second_score",
        ),
        sa.CheckConstraint(
            "top_score IS NULL OR (top_score >= 0 AND top_score <= 1)",
            name="ck_fraud_type_results_top_score",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fraud_type_classification_results_created_at"),
        "fraud_type_classification_results",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_type_classification_results_fraud_type"),
        "fraud_type_classification_results",
        ["fraud_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_type_classification_results_rule_set_version"),
        "fraud_type_classification_results",
        ["rule_set_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_type_classification_results_status"),
        "fraud_type_classification_results",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fraud_type_classification_results_transaction_id"),
        "fraud_type_classification_results",
        ["transaction_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_fraud_type_classification_results_transaction_id"),
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        op.f("ix_fraud_type_classification_results_status"),
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        op.f("ix_fraud_type_classification_results_rule_set_version"),
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        op.f("ix_fraud_type_classification_results_fraud_type"),
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        op.f("ix_fraud_type_classification_results_created_at"),
        table_name="fraud_type_classification_results",
    )
    op.drop_table("fraud_type_classification_results")

    op.drop_index(
        op.f("ix_fraud_rule_components_rule_id"),
        table_name="fraud_rule_components",
    )
    op.drop_table("fraud_rule_components")

    op.drop_index(
        op.f("ix_fraud_rules_rule_set_id"),
        table_name="fraud_rules",
    )
    op.drop_index(
        op.f("ix_fraud_rules_enabled"),
        table_name="fraud_rules",
    )
    op.drop_table("fraud_rules")

    op.drop_index(
        op.f("ix_fraud_rule_sets_version"),
        table_name="fraud_rule_sets",
    )
    op.drop_index(
        op.f("ix_fraud_rule_sets_status"),
        table_name="fraud_rule_sets",
    )
    op.drop_table("fraud_rule_sets")
