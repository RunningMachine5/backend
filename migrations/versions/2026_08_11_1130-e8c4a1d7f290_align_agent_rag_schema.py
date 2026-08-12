"""align Agent and RAG persistence contracts

Revision ID: e8c4a1d7f290
Revises: b7e1c4a9d305
Create Date: 2026-08-11 11:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e8c4a1d7f290"
down_revision: Union[str, Sequence[str], None] = "b7e1c4a9d305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    """복수 문서 메타데이터와 Agent 필수 입력 제약을 반영한다."""

    op.drop_constraint("ck_documents_audience", "documents", type_="check")

    op.alter_column(
        "documents",
        "fraud_type",
        existing_type=sa.String(length=64),
        type_=JSONB,
        postgresql_using=(
            "CASE WHEN fraud_type IS NULL THEN '[]'::jsonb "
            "ELSE jsonb_build_array(fraud_type) END"
        ),
        nullable=False,
    )
    op.alter_column(
        "documents",
        "fraud_type",
        new_column_name="fraud_types",
        existing_type=JSONB,
        existing_nullable=False,
    )

    op.alter_column(
        "documents",
        "audience",
        existing_type=sa.String(length=16),
        type_=JSONB,
        postgresql_using="jsonb_build_array(audience)",
        nullable=False,
    )
    op.alter_column(
        "documents",
        "audience",
        new_column_name="audiences",
        existing_type=JSONB,
        existing_nullable=False,
    )

    op.execute(
        "UPDATE fraud_type_score_results "
        "SET rule_filter_status = 'APPLIED' "
        "WHERE rule_filter_status IS NULL"
    )
    op.alter_column(
        "fraud_type_score_results",
        "rule_filter_status",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_fraud_type_score_results_filter_status",
        "fraud_type_score_results",
        "rule_filter_status IN "
        "('APPLIED', 'SKIPPED_NOT_FRAUD', 'FAILED')",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM agent_cases
                WHERE risk_score IS NULL OR risk_grade IS NULL
            ) THEN
                RAISE EXCEPTION
                    'agent_cases의 NULL 위험정보를 보정한 뒤 마이그레이션해야 한다.';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_agent_cases_risk_score",
        "agent_cases",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_cases_risk_grade",
        "agent_cases",
        type_="check",
    )
    op.alter_column(
        "agent_cases",
        "risk_score",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "agent_cases",
        "risk_grade",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_agent_cases_risk_score",
        "agent_cases",
        "risk_score BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "ck_agent_cases_risk_grade",
        "agent_cases",
        "risk_grade IN ('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')",
    )


def downgrade() -> None:
    """직전 단일 문서 분류와 nullable Agent 입력 구조로 되돌린다."""

    op.drop_constraint(
        "ck_agent_cases_risk_grade",
        "agent_cases",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_cases_risk_score",
        "agent_cases",
        type_="check",
    )
    op.alter_column(
        "agent_cases",
        "risk_grade",
        existing_type=sa.String(length=16),
        nullable=True,
    )
    op.alter_column(
        "agent_cases",
        "risk_score",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_agent_cases_risk_grade",
        "agent_cases",
        "risk_grade IS NULL OR risk_grade IN "
        "('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')",
    )
    op.create_check_constraint(
        "ck_agent_cases_risk_score",
        "agent_cases",
        "risk_score IS NULL OR risk_score BETWEEN 0 AND 100",
    )

    op.drop_constraint(
        "ck_fraud_type_score_results_filter_status",
        "fraud_type_score_results",
        type_="check",
    )
    op.alter_column(
        "fraud_type_score_results",
        "rule_filter_status",
        existing_type=sa.String(length=32),
        nullable=True,
    )

    op.alter_column(
        "documents",
        "audiences",
        new_column_name="audience",
        existing_type=JSONB,
        existing_nullable=False,
    )
    op.alter_column(
        "documents",
        "audience",
        existing_type=JSONB,
        type_=sa.String(length=16),
        postgresql_using="COALESCE(audience ->> 0, 'COMMON')",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_documents_audience",
        "documents",
        "audience IN ('MONITORING', 'CUSTOMER', 'COMMON')",
    )

    op.alter_column(
        "documents",
        "fraud_types",
        new_column_name="fraud_type",
        existing_type=JSONB,
        existing_nullable=False,
    )
    op.alter_column(
        "documents",
        "fraud_type",
        existing_type=JSONB,
        type_=sa.String(length=64),
        postgresql_using="NULLIF(fraud_type ->> 0, '')",
        nullable=True,
    )


__all__ = ["upgrade", "downgrade"]
