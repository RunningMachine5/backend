"""HTTP contracts for fraud-rule administration and rule explanations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import isclose, isfinite
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.model.fraud_rule import FraudRuleSetStatus
from app.dto.ml_features import MLTransactionFeatures


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

    @model_validator(mode="after")
    def validate_type_summary(self) -> Self:
        averages = (
            self.active_average_score,
            self.draft_average_score,
            self.average_score_delta,
            self.max_absolute_score_delta,
        )
        if any(value is not None and not isfinite(value) for value in averages):
            raise ValueError("유형별 점수 요약은 유한한 값이어야 합니다.")
        if self.matched_transaction_count_delta != (
            self.draft_matched_transaction_count
            - self.active_matched_transaction_count
        ):
            raise ValueError("유형별 매칭 변화량이 ACTIVE·DRAFT 건수와 다릅니다.")
        if (self.active_average_score is None) != (
            self.draft_average_score is None
        ) or (self.active_average_score is None) != (
            self.average_score_delta is None
        ):
            raise ValueError("ACTIVE·DRAFT 평균과 평균 변화량의 null 상태가 다릅니다.")
        if self.active_average_score is not None and not isclose(
            self.average_score_delta,
            self.draft_average_score - self.active_average_score,
            abs_tol=1e-10,
        ):
            raise ValueError("평균 점수 변화량이 ACTIVE·DRAFT 평균과 다릅니다.")
        return self


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

    @model_validator(mode="after")
    def validate_component_impact(self) -> Self:
        if self.matched_transaction_count_delta != (
            self.draft_matched_transaction_count
            - self.active_matched_transaction_count
        ):
            raise ValueError("구성요소 매칭 변화량이 ACTIVE·DRAFT 건수와 다릅니다.")
        if self.matched_transaction_count_delta != (
            self.newly_matched_transaction_count
            - self.no_longer_matched_transaction_count
        ):
            raise ValueError("구성요소 순매칭 변화량이 신규·이탈 건수와 다릅니다.")
        if self.active_present != (self.active_weight is not None):
            raise ValueError("ACTIVE 구성요소 존재 여부와 가중치가 일치하지 않습니다.")
        if self.draft_present != (self.draft_weight is not None):
            raise ValueError("DRAFT 구성요소 존재 여부와 가중치가 일치하지 않습니다.")
        if not self.active_present and self.active_matched_transaction_count:
            raise ValueError("없는 ACTIVE 구성요소는 거래에 매칭될 수 없습니다.")
        if not self.draft_present and self.draft_matched_transaction_count:
            raise ValueError("없는 DRAFT 구성요소는 거래에 매칭될 수 없습니다.")
        return self


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

    @model_validator(mode="after")
    def require_actual_change(self) -> Self:
        for changes in (
            self.added_matched_components,
            self.removed_matched_components,
        ):
            if any(not keys for keys in changes.values()):
                raise ValueError("구성요소 변경 맵에는 빈 유형 키를 포함할 수 없습니다.")
            if any(keys != sorted(set(keys)) for keys in changes.values()):
                raise ValueError("변경 구성요소는 중복 없이 정렬되어야 합니다.")
        for type_code in (
            set(self.added_matched_components)
            & set(self.removed_matched_components)
        ):
            if set(self.added_matched_components[type_code]) & set(
                self.removed_matched_components[type_code]
            ):
                raise ValueError("같은 구성요소를 추가와 제거로 동시에 표시할 수 없습니다.")

        expected_score_changed = any(self.score_deltas.values())
        expected_evidence_changed = any(
            self.added_matched_components.values()
        ) or any(self.removed_matched_components.values())
        if self.score_changed != expected_score_changed:
            raise ValueError("점수 변경 여부가 유형별 점수 변화량과 일치하지 않습니다.")
        if self.evidence_changed != expected_evidence_changed:
            raise ValueError(
                "근거 변경 여부가 추가·제거된 구성요소와 일치하지 않습니다."
            )

        type_codes = set(self.active_type_scores) | set(self.draft_type_scores)
        if set(self.score_deltas) != type_codes:
            raise ValueError("유형별 점수와 변화량의 유형 코드가 일치하지 않습니다.")
        for type_code, delta in self.score_deltas.items():
            expected_delta = round(
                self.draft_type_scores.get(type_code, 0.0)
                - self.active_type_scores.get(type_code, 0.0),
                10,
            )
            if not isclose(delta, expected_delta, abs_tol=1e-10):
                raise ValueError("유형별 점수 변화량이 ACTIVE·DRAFT 점수와 다릅니다.")
        expected_max_delta = max(
            (abs(value) for value in self.score_deltas.values()),
            default=0.0,
        )
        if not isclose(
            self.max_absolute_score_delta,
            expected_max_delta,
            abs_tol=1e-10,
        ):
            raise ValueError(
                "최대 점수 변화량이 유형별 점수 변화량과 일치하지 않습니다."
            )
        score_values = [
            *self.active_type_scores.values(),
            *self.draft_type_scores.values(),
        ]
        if any(
            not isfinite(value) or not 0.0 <= value <= 1.0 for value in score_values
        ):
            raise ValueError("유형 점수는 0과 1 사이의 유한한 값이어야 합니다.")
        if any(
            not isfinite(value) or not -1.0 <= value <= 1.0
            for value in self.score_deltas.values()
        ):
            raise ValueError("유형 점수 변화량은 -1과 1 사이의 유한한 값이어야 합니다.")
        if not self.score_changed and not self.evidence_changed:
            raise ValueError("상세 결과에는 변경된 거래만 포함할 수 있습니다.")
        return self


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

    @model_validator(mode="after")
    def validate_replay_counts(self) -> Self:
        if self.active_rule_set.rule_set_id == self.draft_rule_set.rule_set_id:
            raise ValueError("ACTIVE와 DRAFT 룰셋은 서로 달라야 합니다.")
        if self.selected_count > self.requested_count:
            raise ValueError("selected_count는 requested_count를 초과할 수 없습니다.")
        if self.has_more and self.selected_count != self.requested_count:
            raise ValueError("has_more가 true이면 요청한 표본 수를 모두 선택해야 합니다.")
        if self.evaluated_count + self.error_count != self.selected_count:
            raise ValueError("평가 건수와 오류 건수의 합은 선택 건수여야 합니다.")
        if self.summary_denominator != self.evaluated_count:
            raise ValueError("요약 통계의 분모는 평가 완료 건수여야 합니다.")
        if self.changed_transaction_count > self.evaluated_count:
            raise ValueError("변경 거래 수는 평가 완료 건수를 초과할 수 없습니다.")
        if (
            max(
                self.score_changed_transaction_count,
                self.evidence_changed_transaction_count,
            )
            > self.changed_transaction_count
        ):
            raise ValueError(
                "점수·근거 변경 건수는 전체 변경 건수를 초과할 수 없습니다."
            )
        if self.changed_transaction_count > (
            self.score_changed_transaction_count
            + self.evidence_changed_transaction_count
        ):
            raise ValueError("전체 변경 건수는 점수·근거 변경 합집합이어야 합니다.")
        if max(self.active_no_match_count, self.draft_no_match_count) > (
            self.evaluated_count
        ):
            raise ValueError("미매칭 거래 수는 평가 완료 건수를 초과할 수 없습니다.")
        expected_rate = (
            round(self.changed_transaction_count / self.evaluated_count, 10)
            if self.evaluated_count
            else None
        )
        if self.changed_transaction_rate != expected_rate:
            raise ValueError("변경 비율은 평가 완료 건수를 기준으로 계산해야 합니다.")
        if self.no_match_count_delta != (
            self.draft_no_match_count - self.active_no_match_count
        ):
            raise ValueError("미매칭 변화량이 ACTIVE·DRAFT 건수와 일치하지 않습니다.")
        if any(
            summary.score_increased_transaction_count
            + summary.score_decreased_transaction_count
            + summary.score_unchanged_transaction_count
            != self.evaluated_count
            for summary in self.type_summaries
        ):
            raise ValueError("유형별 점수 변화 건수 합이 평가 완료 건수와 다릅니다.")
        if any(
            max(
                summary.active_matched_transaction_count,
                summary.draft_matched_transaction_count,
            )
            > self.evaluated_count
            for summary in self.type_summaries
        ):
            raise ValueError("유형별 매칭 건수가 평가 완료 건수를 초과합니다.")
        if any(
            max(
                impact.active_matched_transaction_count,
                impact.draft_matched_transaction_count,
                impact.newly_matched_transaction_count,
                impact.no_longer_matched_transaction_count,
            )
            > self.evaluated_count
            for impact in self.component_impacts
        ):
            raise ValueError(
                "구성요소별 매칭 건수는 평가 완료 건수를 초과할 수 없습니다."
            )
        if len({item.type_code for item in self.type_summaries}) != len(
            self.type_summaries
        ):
            raise ValueError("유형별 요약에 중복된 type_code가 있습니다.")
        if len(
            {(item.type_code, item.component_key) for item in self.component_impacts}
        ) != len(self.component_impacts):
            raise ValueError("구성요소 영향에 중복된 항목이 있습니다.")
        changed_ids = [
            item.transaction_id for item in self.changed_transaction_details
        ]
        error_ids = [item.transaction_id for item in self.error_details]
        if len(set(changed_ids)) != len(changed_ids) or len(set(error_ids)) != len(
            error_ids
        ):
            raise ValueError("리플레이 상세에는 같은 거래를 중복할 수 없습니다.")
        if set(changed_ids) & set(error_ids):
            raise ValueError("같은 거래는 변경 상세와 오류 상세에 함께 올 수 없습니다.")
        if len(self.changed_transaction_details) > self.detail_limit:
            raise ValueError("변경 상세는 detail_limit을 초과할 수 없습니다.")
        if len(self.error_details) > self.detail_limit:
            raise ValueError("오류 상세는 detail_limit을 초과할 수 없습니다.")
        if len(self.changed_transaction_details) > self.changed_transaction_count:
            raise ValueError("변경 상세는 전체 변경 건수를 초과할 수 없습니다.")
        if len(self.error_details) > self.error_count:
            raise ValueError("오류 상세는 전체 오류 건수를 초과할 수 없습니다.")
        if self.changed_details_truncated != (
            self.changed_transaction_count > len(self.changed_transaction_details)
        ):
            raise ValueError("변경 상세 잘림 상태가 건수와 일치하지 않습니다.")
        if self.error_details_truncated != (self.error_count > len(self.error_details)):
            raise ValueError("오류 상세 잘림 상태가 건수와 일치하지 않습니다.")
        return self


def expression_to_json(expression: RuleExpression) -> dict[str, Any]:
    """Serialize a validated expression without irrelevant null properties."""

    return expression.model_dump(mode="json", exclude_none=True)


__all__ = [
    "GROUP_OPERATORS",
    "FraudRuleComponentCreate",
    "FraudRuleComponentResponse",
    "FraudRuleComponentUpdate",
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
