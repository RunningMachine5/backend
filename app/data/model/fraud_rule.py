"""Persistence models for versioned fraud-type rule sets."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, JSON_COLUMN


class FraudRuleSetStatus(str, Enum):
    """Lifecycle of an immutable-after-activation rule-set version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class FraudRuleSet(SQLModel, table=True):
    """Versioned container for fraud-type scoring rules."""

    __tablename__ = "fraud_rule_sets"
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    version: int = Field(gt=0, unique=True, index=True)
    status: FraudRuleSetStatus = Field(
        default=FraudRuleSetStatus.DRAFT,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    activated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class FraudRule(SQLModel, table=True):
    """One dynamically managed fraud type within a rule set."""

    __tablename__ = "fraud_rules"
    __table_args__ = (
        UniqueConstraint(
            "rule_set_id",
            "type_code",
            name="uq_fraud_rules_rule_set_type_code",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    rule_set_id: int = Field(
        foreign_key="fraud_rule_sets.id",
        ondelete="CASCADE",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )
    type_code: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = Field(default=True, index=True)
    sort_order: int = Field(default=0, ge=0)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FraudRuleComponent(SQLModel, table=True):
    """Weighted, nested condition expression belonging to a fraud rule."""

    __tablename__ = "fraud_rule_components"
    __table_args__ = (
        UniqueConstraint(
            "rule_id",
            "component_key",
            name="uq_fraud_rule_components_rule_key",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    rule_id: int = Field(
        foreign_key="fraud_rules.id",
        ondelete="CASCADE",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )
    component_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    condition_expression: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON_COLUMN, nullable=False),
    )
    weight: float = Field(sa_column=Column(Float, nullable=False))
    sort_order: int = Field(default=0, ge=0)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FraudTypeScoreResult(SQLModel, table=True):
    """사기 거래에 대해 계산한 모든 유형별 룰 점수를 저장한다."""

    __tablename__ = "fraud_type_score_results"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            name="uq_fraud_type_score_results_transaction_id",
        ),
        CheckConstraint(
            "rule_filter_status IN "
            "('APPLIED', 'SKIPPED_NOT_FRAUD', 'FAILED')",
            name="ck_fraud_type_score_results_filter_status",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    transaction_id: int = Field(
        foreign_key="transactions.id",
        ondelete="CASCADE",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )
    rule_set_id: int = Field(
        foreign_key="fraud_rule_sets.id",
        ondelete="RESTRICT",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )
    rule_filter_status: str = Field(
        max_length=32,
        nullable=False,
    )
    primary_fraud_type: str | None = Field(
        default=None,
        max_length=64,
        index=True,
    )
    type_scores: dict[str, float] = Field(
        default_factory=dict,
        sa_column=Column(JSON_COLUMN, nullable=False),
    )
    matched_components: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column(JSON_COLUMN, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


__all__ = [
    "FraudRule",
    "FraudRuleComponent",
    "FraudRuleSet",
    "FraudRuleSetStatus",
    "FraudTypeScoreResult",
]
