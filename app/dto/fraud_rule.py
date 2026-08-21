"""HTTP contracts for fraud-rule administration and rule explanations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.model.fraud_rule import FraudRuleSetStatus


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


class RulePatternStatisticsItemRequest(BaseModel):
    """통계를 계산할 화면상의 패턴 한 개."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component_key: str = Field(min_length=1, max_length=64)
    condition_expression: RuleExpression


class RulePatternStatisticsRequest(BaseModel):
    """같은 최근 거래 표본으로 함께 계산할 패턴 목록."""

    model_config = ConfigDict(extra="forbid")

    sample_size: int = Field(default=1000, strict=True, ge=1, le=1000)
    patterns: list[RulePatternStatisticsItemRequest] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def reject_duplicate_component_keys(self) -> Self:
        keys = [pattern.component_key for pattern in self.patterns]
        if len(keys) != len(set(keys)):
            raise ValueError("component_key는 통계 요청에서 중복될 수 없습니다.")
        return self


class RulePatternValueCountResponse(BaseModel):
    value: str | int | bool
    count: int = Field(ge=0, le=1000)
    rate: float | None = Field(default=None, ge=0.0, le=1.0)


class RulePatternFeatureStatisticsResponse(BaseModel):
    field: str
    value_type: Literal["integer", "number", "boolean", "enum"]
    value_count: int = Field(ge=0, le=1000)
    average: float | None = None
    median: float | None = None
    p90: float | None = None
    value_counts: list[RulePatternValueCountResponse] = Field(default_factory=list)


class RulePatternStatisticsItemResponse(BaseModel):
    component_key: str
    matched_count: int = Field(ge=0, le=1000)
    matched_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    feature_statistics: RulePatternFeatureStatisticsResponse | None = None


class RulePatternStatisticsResponse(BaseModel):
    """최근 ML 양성 거래에서 계산한 읽기 전용 패턴 통계."""

    selection_basis: Literal["LATEST_ML_POSITIVE"] = "LATEST_ML_POSITIVE"
    requested_count: int = Field(ge=1, le=1000)
    sample_count: int = Field(ge=0, le=1000)
    has_more: bool
    patterns: list[RulePatternStatisticsItemResponse]


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


class FraudRuleComponentsUpdate(BaseModel):
    """기존 사기유형에 저장할 전체 패턴 목록."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    components: list[FraudRuleComponentCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_component_keys(self) -> Self:
        keys = [component.component_key for component in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("component_key는 한 룰 안에서 중복될 수 없습니다.")
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


class FraudRuleComponentWeightUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component_key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]{1,63}$",
    )
    weight: float = Field(gt=0.0, le=1.0)


class FraudRuleWeightUpdate(BaseModel):
    """운영자가 바꿀 수 있는 기존 구성요소의 가중치 목록."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    components: list[FraudRuleComponentWeightUpdate] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_component_keys(self) -> Self:
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


class FraudRuleReplayRequest(BaseModel):
    """최신 ML 양성 거래 표본과 상세 응답 크기."""

    model_config = ConfigDict(extra="forbid")

    sample_size: int = Field(default=1000, strict=True, ge=1, le=1000)
    detail_limit: int = Field(default=100, strict=True, ge=0, le=100)


class FraudRuleReplayRuleSetResponse(BaseModel):
    rule_set_id: int = Field(gt=0)
    version: int = Field(gt=0)
    updated_at: datetime


class FraudRuleReplayTypeSummaryResponse(BaseModel):
    type_code: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    active_enabled: bool
    draft_enabled: bool
    active_average_score: float | None = Field(default=None, ge=0.0, le=1.0)
    draft_average_score: float | None = Field(default=None, ge=0.0, le=1.0)
    average_score_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    active_matched_transaction_count: int = Field(ge=0)
    draft_matched_transaction_count: int = Field(ge=0)
    matched_transaction_count_delta: int
    score_increased_transaction_count: int = Field(ge=0)
    score_decreased_transaction_count: int = Field(ge=0)
    score_unchanged_transaction_count: int = Field(ge=0)
    max_absolute_score_delta: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

class FraudRuleReplayComponentImpactResponse(BaseModel):
    type_code: str = Field(min_length=1, max_length=64)
    component_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    active_present: bool
    draft_present: bool
    active_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    draft_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    definition_changed: bool
    active_matched_transaction_count: int = Field(ge=0)
    draft_matched_transaction_count: int = Field(ge=0)
    matched_transaction_count_delta: int
    newly_matched_transaction_count: int = Field(ge=0)
    no_longer_matched_transaction_count: int = Field(ge=0)

class FraudRuleReplayChangedTransactionResponse(BaseModel):
    transaction_id: int = Field(gt=0)
    transaction_datetime: datetime
    score_changed: bool
    evidence_changed: bool
    max_absolute_score_delta: float = Field(ge=0.0, le=1.0)
    active_type_scores: dict[str, float] = Field(default_factory=dict)
    draft_type_scores: dict[str, float] = Field(default_factory=dict)
    score_deltas: dict[str, float] = Field(default_factory=dict)
    added_matched_components: dict[str, list[str]] = Field(default_factory=dict)
    removed_matched_components: dict[str, list[str]] = Field(default_factory=dict)

class FraudRuleReplayErrorDetailResponse(BaseModel):
    transaction_id: int = Field(gt=0)
    transaction_datetime: datetime
    error: str = Field(min_length=1, max_length=1000)


class FraudRuleReplayResponse(BaseModel):
    """같은 과거 표본에 ACTIVE와 DRAFT를 적용한 읽기 전용 비교 결과."""

    selection_basis: Literal["LATEST_ML_POSITIVE"] = "LATEST_ML_POSITIVE"
    aggregation_basis: Literal["EVALUATED_ONLY"] = "EVALUATED_ONLY"
    active_rule_set: FraudRuleReplayRuleSetResponse
    draft_rule_set: FraudRuleReplayRuleSetResponse
    requested_count: int = Field(ge=1, le=1000)
    selected_count: int = Field(ge=0, le=1000)
    evaluated_count: int = Field(ge=0, le=1000)
    error_count: int = Field(ge=0, le=1000)
    summary_denominator: int = Field(ge=0, le=1000)
    detail_limit: int = Field(ge=0, le=100)
    has_more: bool
    changed_transaction_count: int = Field(ge=0, le=1000)
    changed_transaction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    score_changed_transaction_count: int = Field(ge=0, le=1000)
    evidence_changed_transaction_count: int = Field(ge=0, le=1000)
    active_no_match_count: int = Field(ge=0, le=1000)
    draft_no_match_count: int = Field(ge=0, le=1000)
    no_match_count_delta: int = Field(ge=-1000, le=1000)
    type_summaries: list[FraudRuleReplayTypeSummaryResponse] = Field(
        default_factory=list
    )
    component_impacts: list[FraudRuleReplayComponentImpactResponse] = Field(
        default_factory=list
    )
    changed_transaction_details: list[FraudRuleReplayChangedTransactionResponse] = (
        Field(default_factory=list)
    )
    error_details: list[FraudRuleReplayErrorDetailResponse] = Field(
        default_factory=list
    )
    changed_details_truncated: bool
    error_details_truncated: bool

def expression_to_json(expression: RuleExpression) -> dict[str, Any]:
    """Serialize a validated expression without irrelevant null properties."""

    return expression.model_dump(mode="json", exclude_none=True)


__all__ = [
    "GROUP_OPERATORS",
    "FraudRuleComponentCreate",
    "FraudRuleComponentResponse",
    "FraudRuleComponentsUpdate",
    "FraudRuleComponentWeightUpdate",
    "FraudRuleCreate",
    "FraudRuleReplayChangedTransactionResponse",
    "FraudRuleReplayComponentImpactResponse",
    "FraudRuleReplayErrorDetailResponse",
    "FraudRuleReplayRequest",
    "FraudRuleReplayResponse",
    "FraudRuleReplayRuleSetResponse",
    "FraudRuleReplayTypeSummaryResponse",
    "FraudRuleResponse",
    "FraudRuleSetDraftCreate",
    "FraudRuleSetResponse",
    "FraudRuleSetSummaryResponse",
    "FraudRuleWeightUpdate",
    "FraudRuleValidationIssue",
    "FraudRuleValidationResponse",
    "RuleExpression",
    "RuleExpressionOperator",
    "RuleFeatureResponse",
    "RulePatternFeatureStatisticsResponse",
    "RulePatternStatisticsItemRequest",
    "RulePatternStatisticsItemResponse",
    "RulePatternStatisticsRequest",
    "RulePatternStatisticsResponse",
    "RulePatternValueCountResponse",
    "expression_to_json",
]
