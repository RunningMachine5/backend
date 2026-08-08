"""store fraud-type rule scores without selecting a representative type

Revision ID: a42d8f6c3e91
Revises: 7f1c9d4a2b6e
Create Date: 2026-08-08 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "a42d8f6c3e91"
down_revision: Union[str, Sequence[str], None] = "7f1c9d4a2b6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 정상 거래의 SKIPPED 행과 룰 실행 실패 행에는 유형별 점수가 없으므로 제거한다.
    op.execute(
        sa.text(
            "DELETE FROM fraud_type_classification_results "
            "WHERE rule_set_version IS NULL "
            "OR CAST(type_scores AS TEXT) IN ('{}', 'null')"
        )
    )

    op.drop_index(
        "ix_fraud_type_classification_results_status",
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        "ix_fraud_type_classification_results_fraud_type",
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        "ix_fraud_type_classification_results_transaction_id",
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        "ix_fraud_type_classification_results_rule_set_version",
        table_name="fraud_type_classification_results",
    )
    op.drop_index(
        "ix_fraud_type_classification_results_created_at",
        table_name="fraud_type_classification_results",
    )

    op.drop_constraint(
        "ck_fraud_type_results_top_score",
        "fraud_type_classification_results",
        type_="check",
    )
    op.drop_constraint(
        "ck_fraud_type_results_second_score",
        "fraud_type_classification_results",
        type_="check",
    )
    op.drop_constraint(
        "ck_fraud_type_results_score_gap",
        "fraud_type_classification_results",
        type_="check",
    )
    for column_name in (
        "status",
        "fraud_type",
        "top_score",
        "second_score",
        "score_gap",
        "error_message",
    ):
        op.drop_column("fraud_type_classification_results", column_name)

    op.alter_column(
        "fraud_type_classification_results",
        "rule_set_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.rename_table(
        "fraud_type_classification_results",
        "fraud_type_score_results",
    )
    op.create_unique_constraint(
        "uq_fraud_type_score_results_transaction_id",
        "fraud_type_score_results",
        ["transaction_id"],
    )
    op.create_index(
        "ix_fraud_type_score_results_transaction_id",
        "fraud_type_score_results",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_score_results_rule_set_version",
        "fraud_type_score_results",
        ["rule_set_version"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_score_results_created_at",
        "fraud_type_score_results",
        ["created_at"],
        unique=False,
    )

    op.drop_constraint(
        "ck_fraud_rule_sets_minimum_score",
        "fraud_rule_sets",
        type_="check",
    )
    op.drop_constraint(
        "ck_fraud_rule_sets_ambiguity_margin",
        "fraud_rule_sets",
        type_="check",
    )
    op.drop_column("fraud_rule_sets", "minimum_score")
    op.drop_column("fraud_rule_sets", "ambiguity_margin")


def downgrade() -> None:
    op.add_column(
        "fraud_rule_sets",
        sa.Column(
            "minimum_score",
            sa.Float(),
            nullable=False,
            server_default="0.5",
        ),
    )
    op.add_column(
        "fraud_rule_sets",
        sa.Column(
            "ambiguity_margin",
            sa.Float(),
            nullable=False,
            server_default="0.1",
        ),
    )
    op.create_check_constraint(
        "ck_fraud_rule_sets_minimum_score",
        "fraud_rule_sets",
        "minimum_score >= 0 AND minimum_score <= 1",
    )
    op.create_check_constraint(
        "ck_fraud_rule_sets_ambiguity_margin",
        "fraud_rule_sets",
        "ambiguity_margin >= 0 AND ambiguity_margin <= 1",
    )

    op.drop_index(
        "ix_fraud_type_score_results_created_at",
        table_name="fraud_type_score_results",
    )
    op.drop_index(
        "ix_fraud_type_score_results_rule_set_version",
        table_name="fraud_type_score_results",
    )
    op.drop_index(
        "ix_fraud_type_score_results_transaction_id",
        table_name="fraud_type_score_results",
    )
    op.drop_constraint(
        "uq_fraud_type_score_results_transaction_id",
        "fraud_type_score_results",
        type_="unique",
    )
    op.rename_table(
        "fraud_type_score_results",
        "fraud_type_classification_results",
    )
    op.alter_column(
        "fraud_type_classification_results",
        "rule_set_version",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="CLASSIFIED"),
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("fraud_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("top_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("second_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("score_gap", sa.Float(), nullable=True),
    )
    op.add_column(
        "fraud_type_classification_results",
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
    )
    op.create_check_constraint(
        "ck_fraud_type_results_top_score",
        "fraud_type_classification_results",
        "top_score IS NULL OR (top_score >= 0 AND top_score <= 1)",
    )
    op.create_check_constraint(
        "ck_fraud_type_results_second_score",
        "fraud_type_classification_results",
        "second_score IS NULL OR (second_score >= 0 AND second_score <= 1)",
    )
    op.create_check_constraint(
        "ck_fraud_type_results_score_gap",
        "fraud_type_classification_results",
        "score_gap IS NULL OR (score_gap >= 0 AND score_gap <= 1)",
    )
    op.create_index(
        "ix_fraud_type_classification_results_transaction_id",
        "fraud_type_classification_results",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_classification_results_rule_set_version",
        "fraud_type_classification_results",
        ["rule_set_version"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_classification_results_created_at",
        "fraud_type_classification_results",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_classification_results_status",
        "fraud_type_classification_results",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_fraud_type_classification_results_fraud_type",
        "fraud_type_classification_results",
        ["fraud_type"],
        unique=False,
    )
