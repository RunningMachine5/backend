"""Persistence models for versioned fraud-type rule sets."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")


class FraudRuleSetStatus(str, Enum):
    """Lifecycle of an immutable-after-activation rule-set version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class FraudRuleSet(SQLModel, table=True):
    """Versioned container for fraud-type scoring rules."""

    __tablename__ = "fraud_rule_sets"
    id: int | None = Field(default=None, primary_key=True)
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
        CheckConstraint("sort_order >= 0", name="ck_fraud_rules_sort_order"),
    )

    id: int | None = Field(default=None, primary_key=True)
    rule_set_id: int = Field(
        foreign_key="fraud_rule_sets.id",
        ondelete="CASCADE",
        index=True,
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
        CheckConstraint(
            "weight > 0 AND weight <= 1",
            name="ck_fraud_rule_components_weight",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_fraud_rule_components_sort_order",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    rule_id: int = Field(
        foreign_key="fraud_rules.id",
        ondelete="CASCADE",
        index=True,
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
    )

    id: int | None = Field(default=None, primary_key=True)
    transaction_id: str = Field(
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
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
    rule_set_version: int = Field(index=True)
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
