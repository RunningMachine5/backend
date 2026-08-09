"""HTTP contracts for fraud-rule administration and rule explanations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.model.fraud_rule import FraudRuleSetStatus
from app.dto.ml_prediction import MLTransactionFeatures


class RuleExpressionOperator(str, Enum):
    AND = "AND"
    OR = "OR"
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    IN = "IN"
    BETWEEN = "BETWEEN"


GROUP_OPERATORS = frozenset(
    {RuleExpressionOperator.AND, RuleExpressionOperator.OR}
)


class RuleExpression(BaseModel):
    """A recursively nested AND/OR group or a single comparison."""

    model_config = ConfigDict(extra="forbid")

    operator: RuleExpressionOperator
    field: str | None = Field(default=None, min_length=1, max_length=128)
    value: str | int | float | bool | list[str | int | float | bool] | None = None
    conditions: list[RuleExpression] | None = None

    @model_validator(mode="after")
    def validate_node_shape(self) -> Self:
        if self.operator in GROUP_OPERATORS:
            if self.field is not None or self.value is not None:
                raise ValueError("AND/OR 그룹에는 field 또는 value를 지정할 수 없습니다.")
            if not self.conditions:
                raise ValueError("AND/OR 그룹에는 하나 이상의 conditions가 필요합니다.")
            return self

        if self.field is None:
            raise ValueError("비교 조건에는 field가 필요합니다.")
        if self.value is None:
            raise ValueError("비교 조건에는 value가 필요합니다.")
        if self.conditions is not None:
            raise ValueError("비교 조건에는 conditions를 지정할 수 없습니다.")
        return self


class RuleFeatureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    display_name: str
    value_type: Literal[
        "integer",
        "number",
        "boolean",
        "enum",
        "datetime",
        "duration",
    ]
    operators: list[RuleExpressionOperator]
    allowed_values: list[str | int | bool] | None = None
    derived: bool = False
    source_fields: list[str] = Field(default_factory=list)


class FraudRuleComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component_key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]{1,63}$",
    )
    name: str = Field(min_length=1, max_length=128)
    condition_expression: RuleExpression
    weight: float = Field(gt=0.0, le=1.0)
    sort_order: int = Field(default=0, ge=0)


class FraudRuleComponentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]{1,63}$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=128)
    condition_expression: RuleExpression | None = None
    weight: float | None = Field(default=None, gt=0.0, le=1.0)
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def reject_empty_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("수정할 구성요소 필드가 하나 이상 필요합니다.")
        return self


class FraudRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{1,63}$",
    )
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)
    components: list[FraudRuleComponentCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_component_keys(self) -> Self:
        keys = [component.component_key for component in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("component_key는 한 룰 안에서 중복될 수 없습니다.")
        return self


class FraudRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{1,63}$",
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    components: list[FraudRuleComponentCreate] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("수정할 룰 필드가 하나 이상 필요합니다.")
        if self.components is not None:
            keys = [component.component_key for component in self.components]
            if len(keys) != len(set(keys)):
                raise ValueError("component_key는 한 룰 안에서 중복될 수 없습니다.")
        return self


class FraudRuleComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    component_key: str
    name: str
    condition_expression: RuleExpression
    weight: float
    sort_order: int
    created_at: datetime
    updated_at: datetime


class FraudRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type_code: str
    display_name: str
    description: str | None
    enabled: bool
    sort_order: int
    components: list[FraudRuleComponentResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FraudRuleSetSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    status: FraudRuleSetStatus
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None


class FraudRuleSetResponse(FraudRuleSetSummaryResponse):
    rules: list[FraudRuleResponse] = Field(default_factory=list)


class FraudRuleSetDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_rule_set_id: int | None = Field(default=None, gt=0)


class FraudRuleValidationIssue(BaseModel):
    path: str
    message: str


class FraudRuleValidationResponse(BaseModel):
    rule_set_id: int
    valid: bool
    issues: list[FraudRuleValidationIssue] = Field(default_factory=list)


class FraudRuleTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_data: MLTransactionFeatures


class FraudRuleTypeScoreResponse(BaseModel):
    type_code: str
    display_name: str
    score: float
    matched_components: list[str] = Field(default_factory=list)


class FraudRuleTestResponse(BaseModel):
    rule_set_version: int
    type_scores: list[FraudRuleTypeScoreResponse]


def expression_to_json(expression: RuleExpression) -> dict[str, Any]:
    """Serialize a validated expression without irrelevant null properties."""

    return expression.model_dump(mode="json", exclude_none=True)


__all__ = [
    "GROUP_OPERATORS",
    "FraudRuleComponentCreate",
    "FraudRuleComponentResponse",
    "FraudRuleComponentUpdate",
    "FraudRuleCreate",
    "FraudRuleResponse",
    "FraudRuleSetDraftCreate",
    "FraudRuleSetResponse",
    "FraudRuleSetSummaryResponse",
    "FraudRuleTestRequest",
    "FraudRuleTestResponse",
    "FraudRuleTypeScoreResponse",
    "FraudRuleUpdate",
    "FraudRuleValidationIssue",
    "FraudRuleValidationResponse",
    "RuleExpression",
    "RuleExpressionOperator",
    "RuleFeatureResponse",
    "expression_to_json",
]
