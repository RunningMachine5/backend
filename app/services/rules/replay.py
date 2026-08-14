"""최신 ML 양성 거래에 ACTIVE·DRAFT 룰을 함께 적용한다.

같은 거래 표본에 두 룰셋을 평가해 점수·매칭 component가 어떻게 달라지는지만
비교한다. 운영 점수 행을 다시 쓰거나 DRAFT를 자동 활성화하지 않는 읽기 전용
사전 점검 기능이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlmodel import Session

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.repositories.transaction import PredictionResultRepository
from app.services.features.ml_feature_assembler import (
    FeatureAssemblyError,
    assemble_ml_features,
)
from app.services.rules.engine import (
    RuleComponentDefinition,
    RuleEngine,
    RuleSetDefinition,
    RuleSetValidationError,
)
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.feature_builder import RuleFeatureError


@dataclass(frozen=True, slots=True)
class _RuleReplayTransaction:
    transaction_id: int
    transaction_datetime: datetime
    active_type_scores: dict[str, float]
    draft_type_scores: dict[str, float]
    score_deltas: dict[str, float]
    active_matched_components: dict[str, list[str]]
    draft_matched_components: dict[str, list[str]]
    score_changed: bool
    evidence_changed: bool
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.score_changed or self.evidence_changed

    @property
    def max_absolute_score_delta(self) -> float:
        return max((abs(value) for value in self.score_deltas.values()), default=0.0)


@dataclass(frozen=True, slots=True)
class RuleReplayChangedTransaction:
    transaction_id: int
    transaction_datetime: datetime
    score_changed: bool
    evidence_changed: bool
    max_absolute_score_delta: float
    active_type_scores: dict[str, float]
    draft_type_scores: dict[str, float]
    score_deltas: dict[str, float]
    added_matched_components: dict[str, list[str]]
    removed_matched_components: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class RuleReplayErrorDetail:
    transaction_id: int
    transaction_datetime: datetime
    error: str


@dataclass(frozen=True, slots=True)
class RuleReplayTypeSummary:
    type_code: str
    display_name: str
    active_enabled: bool
    draft_enabled: bool
    active_average_score: float | None
    draft_average_score: float | None
    average_score_delta: float | None
    active_matched_transaction_count: int
    draft_matched_transaction_count: int
    matched_transaction_count_delta: int
    score_increased_transaction_count: int
    score_decreased_transaction_count: int
    score_unchanged_transaction_count: int
    max_absolute_score_delta: float | None


@dataclass(frozen=True, slots=True)
class RuleReplayComponentImpact:
    type_code: str
    component_key: str
    display_name: str
    active_present: bool
    draft_present: bool
    active_weight: float | None
    draft_weight: float | None
    definition_changed: bool
    active_matched_transaction_count: int
    draft_matched_transaction_count: int
    matched_transaction_count_delta: int
    newly_matched_transaction_count: int
    no_longer_matched_transaction_count: int


@dataclass(frozen=True, slots=True)
class RuleReplayResult:
    requested_count: int
    selected_count: int
    evaluated_count: int
    error_count: int
    has_more: bool
    changed_transaction_count: int
    changed_transaction_rate: float | None
    score_changed_transaction_count: int
    evidence_changed_transaction_count: int
    active_no_match_count: int
    draft_no_match_count: int
    no_match_count_delta: int
    type_summaries: list[RuleReplayTypeSummary]
    component_impacts: list[RuleReplayComponentImpact]
    changed_transaction_details: list[RuleReplayChangedTransaction]
    error_details: list[RuleReplayErrorDetail]
    changed_details_truncated: bool
    error_details_truncated: bool


def _ordered_type_metadata(
    active_definition: RuleSetDefinition,
    draft_definition: RuleSetDefinition,
) -> tuple[list[str], dict[str, str], set[str], set[str]]:
    type_codes: list[str] = []
    display_names: dict[str, str] = {}
    for definition in (active_definition, draft_definition):
        for rule in definition.rules:
            if not rule.enabled:
                continue
            if rule.type_code not in display_names:
                type_codes.append(rule.type_code)
            display_names[rule.type_code] = rule.display_name
    active_enabled = {
        rule.type_code for rule in active_definition.rules if rule.enabled
    }
    draft_enabled = {rule.type_code for rule in draft_definition.rules if rule.enabled}
    return type_codes, display_names, active_enabled, draft_enabled


def _component_metadata(
    definition: RuleSetDefinition,
) -> dict[tuple[str, str], RuleComponentDefinition]:
    return {
        (rule.type_code, component.component_key): component
        for rule in definition.rules
        if rule.enabled
        for component in rule.components
    }


def _definition_changed(
    active: RuleComponentDefinition | None,
    draft: RuleComponentDefinition | None,
) -> bool:
    if active is None or draft is None:
        return active is not draft
    return (
        active.name != draft.name
        or active.weight != draft.weight
        or dict(active.condition_expression) != dict(draft.condition_expression)
    )


def _component_changes(
    active: Mapping[str, list[str]],
    draft: Mapping[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """구성요소 순서와 빈 유형 키를 무시하고 실제 추가·이탈만 계산한다."""

    type_codes = list(dict.fromkeys([*active, *draft]))
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    for type_code in type_codes:
        active_keys = set(active.get(type_code, []))
        draft_keys = set(draft.get(type_code, []))
        added_keys = sorted(draft_keys - active_keys)
        removed_keys = sorted(active_keys - draft_keys)
        if added_keys:
            added[type_code] = added_keys
        if removed_keys:
            removed[type_code] = removed_keys
    return added, removed


def _assembly_error(
    *,
    transaction: Transaction,
    customer: Customer | None,
    source_account: Account | None,
    recipient_account: Account | None,
    derived: DerivedFeatures | None,
) -> FeatureAssemblyError:
    missing: list[str] = []
    if customer is None:
        missing.append("customer")
    if source_account is None:
        missing.append("source_account")
    if recipient_account is None:
        missing.append("recipient_account")
    if derived is None:
        missing.append("derived_features")
    return FeatureAssemblyError(
        "raw60 Feature 조립에 필요한 행이 없습니다: "
        f"{transaction.id} ({', '.join(missing)})"
    )


def _score_transaction(
    *,
    engine: RuleEngine,
    transaction: Transaction,
    customer: Customer | None,
    source_account: Account | None,
    recipient_account: Account | None,
    derived: DerivedFeatures | None,
    active_definition: RuleSetDefinition,
    draft_definition: RuleSetDefinition,
    type_codes: list[str],
) -> _RuleReplayTransaction:
    try:
        if (
            customer is None
            or source_account is None
            or recipient_account is None
            or derived is None
        ):
            raise _assembly_error(
                transaction=transaction,
                customer=customer,
                source_account=source_account,
                recipient_account=recipient_account,
                derived=derived,
            )
        features = assemble_ml_features(
            customer=customer,
            source_account=source_account,
            recipient_account=recipient_account,
            transaction=transaction,
            derived=derived,
        )
        context = engine.feature_builder.build(features)
        # 두 룰셋은 반드시 위에서 한 번 만든 동일 컨텍스트를 평가한다.
        active_result = engine.score_validated_context(context, active_definition)
        draft_result = engine.score_validated_context(context, draft_definition)
    except (
        FeatureAssemblyError,
        ValidationError,
        RuleSetValidationError,
        RuleExpressionError,
        RuleFeatureError,
    ) as exc:
        return _RuleReplayTransaction(
            transaction_id=transaction.id,
            transaction_datetime=transaction.transaction_datetime,
            active_type_scores={},
            draft_type_scores={},
            score_deltas={},
            active_matched_components={},
            draft_matched_components={},
            score_changed=False,
            evidence_changed=False,
            error=str(exc)[:1000],
        )

    active_scores = dict(active_result.type_scores)
    draft_scores = dict(draft_result.type_scores)
    active_components = dict(active_result.matched_components)
    draft_components = dict(draft_result.matched_components)
    added_components, removed_components = _component_changes(
        active_components,
        draft_components,
    )
    score_deltas = {
        type_code: round(
            draft_scores.get(type_code, 0.0) - active_scores.get(type_code, 0.0),
            10,
        )
        for type_code in type_codes
    }
    return _RuleReplayTransaction(
        transaction_id=transaction.id,
        transaction_datetime=transaction.transaction_datetime,
        active_type_scores=active_scores,
        draft_type_scores=draft_scores,
        score_deltas=score_deltas,
        active_matched_components=active_components,
        draft_matched_components=draft_components,
        score_changed=any(score_deltas.values()),
        evidence_changed=bool(added_components or removed_components),
    )


def _changed_detail(
    result: _RuleReplayTransaction,
) -> RuleReplayChangedTransaction:
    added, removed = _component_changes(
        result.active_matched_components,
        result.draft_matched_components,
    )
    return RuleReplayChangedTransaction(
        transaction_id=result.transaction_id,
        transaction_datetime=result.transaction_datetime,
        score_changed=result.score_changed,
        evidence_changed=result.evidence_changed,
        max_absolute_score_delta=result.max_absolute_score_delta,
        active_type_scores=result.active_type_scores,
        draft_type_scores=result.draft_type_scores,
        score_deltas=result.score_deltas,
        added_matched_components=added,
        removed_matched_components=removed,
    )


def _type_summaries(
    *,
    successful: list[_RuleReplayTransaction],
    type_codes: list[str],
    display_names: Mapping[str, str],
    active_enabled: set[str],
    draft_enabled: set[str],
) -> list[RuleReplayTypeSummary]:
    divisor = len(successful)
    summaries: list[RuleReplayTypeSummary] = []
    for type_code in type_codes:
        deltas = [result.score_deltas[type_code] for result in successful]
        active_total = sum(
            result.active_type_scores.get(type_code, 0.0) for result in successful
        )
        draft_total = sum(
            result.draft_type_scores.get(type_code, 0.0) for result in successful
        )
        active_average = round(active_total / divisor, 10) if divisor else None
        draft_average = round(draft_total / divisor, 10) if divisor else None
        active_matched = sum(
            result.active_type_scores.get(type_code, 0.0) > 0 for result in successful
        )
        draft_matched = sum(
            result.draft_type_scores.get(type_code, 0.0) > 0 for result in successful
        )
        summaries.append(
            RuleReplayTypeSummary(
                type_code=type_code,
                display_name=display_names[type_code],
                active_enabled=type_code in active_enabled,
                draft_enabled=type_code in draft_enabled,
                active_average_score=active_average,
                draft_average_score=draft_average,
                average_score_delta=(
                    round(draft_average - active_average, 10)
                    if active_average is not None and draft_average is not None
                    else None
                ),
                active_matched_transaction_count=active_matched,
                draft_matched_transaction_count=draft_matched,
                matched_transaction_count_delta=draft_matched - active_matched,
                score_increased_transaction_count=sum(delta > 0 for delta in deltas),
                score_decreased_transaction_count=sum(delta < 0 for delta in deltas),
                score_unchanged_transaction_count=sum(delta == 0 for delta in deltas),
                max_absolute_score_delta=(
                    max((abs(delta) for delta in deltas), default=0.0)
                    if divisor
                    else None
                ),
            )
        )
    return summaries


def _component_impacts(
    *,
    successful: list[_RuleReplayTransaction],
    active_definition: RuleSetDefinition,
    draft_definition: RuleSetDefinition,
) -> list[RuleReplayComponentImpact]:
    active_components = _component_metadata(active_definition)
    draft_components = _component_metadata(draft_definition)
    keys = list(dict.fromkeys([*active_components, *draft_components]))
    impacts: list[RuleReplayComponentImpact] = []
    for type_code, component_key in keys:
        active = active_components.get((type_code, component_key))
        draft = draft_components.get((type_code, component_key))
        active_count = sum(
            component_key in result.active_matched_components.get(type_code, [])
            for result in successful
        )
        draft_count = sum(
            component_key in result.draft_matched_components.get(type_code, [])
            for result in successful
        )
        newly_matched = sum(
            component_key not in result.active_matched_components.get(type_code, [])
            and component_key in result.draft_matched_components.get(type_code, [])
            for result in successful
        )
        no_longer_matched = sum(
            component_key in result.active_matched_components.get(type_code, [])
            and component_key not in result.draft_matched_components.get(type_code, [])
            for result in successful
        )
        impacts.append(
            RuleReplayComponentImpact(
                type_code=type_code,
                component_key=component_key,
                display_name=(draft or active).name,
                active_present=active is not None,
                draft_present=draft is not None,
                active_weight=active.weight if active is not None else None,
                draft_weight=draft.weight if draft is not None else None,
                definition_changed=_definition_changed(active, draft),
                active_matched_transaction_count=active_count,
                draft_matched_transaction_count=draft_count,
                matched_transaction_count_delta=draft_count - active_count,
                newly_matched_transaction_count=newly_matched,
                no_longer_matched_transaction_count=no_longer_matched,
            )
        )
    return sorted(
        impacts,
        key=lambda item: (
            not item.definition_changed,
            -abs(item.matched_transaction_count_delta),
            item.type_code,
            item.component_key,
        ),
    )


def replay_rule_sets(
    *,
    session: Session,
    active_definition: RuleSetDefinition,
    draft_definition: RuleSetDefinition,
    sample_size: int,
    detail_limit: int,
) -> RuleReplayResult:
    """한 번 고정한 표본을 두 룰셋으로 평가하며 어떤 DB 행도 변경하지 않는다."""

    selected, has_more = PredictionResultRepository(
        session
    ).latest_positive_feature_rows(limit=sample_size)
    type_codes, display_names, active_enabled, draft_enabled = _ordered_type_metadata(
        active_definition,
        draft_definition,
    )
    engine = RuleEngine()
    # RuleSetDefinition은 frozen dataclass이므로 표본 전체에서 안전하게 재사용한다.
    # 행마다 두 번 재검증하지 않고 실행 시작 시 각 정의를 한 번만 검증한다.
    engine.validate_rule_set(active_definition)
    engine.validate_rule_set(draft_definition)
    results = [
        _score_transaction(
            engine=engine,
            transaction=transaction,
            customer=customer,
            source_account=source_account,
            recipient_account=recipient_account,
            derived=derived,
            active_definition=active_definition,
            draft_definition=draft_definition,
            type_codes=type_codes,
        )
        for transaction, customer, source_account, recipient_account, derived in selected
    ]
    successful = [result for result in results if result.error is None]
    errors = [result for result in results if result.error is not None]
    changed = [result for result in successful if result.changed]
    ordered_changed = [
        result
        for _, result in sorted(
            enumerate(changed),
            key=lambda item: (-item[1].max_absolute_score_delta, item[0]),
        )
    ]
    active_no_match = sum(
        not any(result.active_type_scores.values()) for result in successful
    )
    draft_no_match = sum(
        not any(result.draft_type_scores.values()) for result in successful
    )
    evaluated_count = len(successful)

    return RuleReplayResult(
        requested_count=sample_size,
        selected_count=len(results),
        evaluated_count=evaluated_count,
        error_count=len(errors),
        has_more=has_more,
        changed_transaction_count=len(changed),
        changed_transaction_rate=(
            round(len(changed) / evaluated_count, 10) if evaluated_count else None
        ),
        score_changed_transaction_count=sum(
            result.score_changed for result in successful
        ),
        evidence_changed_transaction_count=sum(
            result.evidence_changed for result in successful
        ),
        active_no_match_count=active_no_match,
        draft_no_match_count=draft_no_match,
        no_match_count_delta=draft_no_match - active_no_match,
        type_summaries=_type_summaries(
            successful=successful,
            type_codes=type_codes,
            display_names=display_names,
            active_enabled=active_enabled,
            draft_enabled=draft_enabled,
        ),
        component_impacts=_component_impacts(
            successful=successful,
            active_definition=active_definition,
            draft_definition=draft_definition,
        ),
        changed_transaction_details=[
            _changed_detail(result) for result in ordered_changed[:detail_limit]
        ],
        error_details=[
            RuleReplayErrorDetail(
                transaction_id=result.transaction_id,
                transaction_datetime=result.transaction_datetime,
                error=result.error,
            )
            for result in errors[:detail_limit]
            if result.error is not None
        ],
        changed_details_truncated=len(changed) > detail_limit,
        error_details_truncated=len(errors) > detail_limit,
    )


__all__ = [
    "RuleReplayChangedTransaction",
    "RuleReplayComponentImpact",
    "RuleReplayErrorDetail",
    "RuleReplayResult",
    "RuleReplayTypeSummary",
    "replay_rule_sets",
]
