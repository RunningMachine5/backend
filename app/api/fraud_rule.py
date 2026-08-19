"""버전이 있는 사기유형 룰을 관리하는 관리자 API.

ACTIVE 룰셋은 실시간 거래 평가에 사용되므로 직접 수정하지 않는다. 관리자는
ACTIVE를 복제한 DRAFT에서 룰을 편집하고, validate·replay로 영향을 확인한
뒤 activate한다. 활성화 시 기존 ACTIVE는 ARCHIVED가 된다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.mlops import require_mlops_admin
from app.core.db import SessionDep
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
)
from app.dto.fraud_rule import (
    FraudRuleComponentCreate,
    FraudRuleComponentResponse,
    FraudRuleCreate,
    FraudRuleReplayRequest,
    FraudRuleReplayResponse,
    FraudRuleReplayRuleSetResponse,
    FraudRuleResponse,
    FraudRuleSetDraftCreate,
    FraudRuleSetResponse,
    FraudRuleSetSummaryResponse,
    FraudRuleUpdate,
    FraudRuleValidationIssue,
    FraudRuleValidationResponse,
    RuleExpressionOperator,
    RuleFeatureResponse,
    expression_to_json,
)
from app.services.rules import repository as rule_repository
from app.services.rules.defaults import DEFAULT_RULE_SET
from app.services.rules.engine import (
    RuleEngine,
    RuleSetDefinition,
    RuleSetValidationError,
)
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.feature_builder import (
    TRANSITION_LEGACY_DERIVED_FEATURES,
    TRANSITION_LEGACY_RAW_ALIASES,
)
from app.services.rules.replay import replay_rule_sets
from app.services.rules.repository import rule_set_definition_from_database

router = APIRouter(
    tags=["fraud-rule-admin"],
    dependencies=[Depends(require_mlops_admin)],
)


_NUMERIC_OPERATORS = [
    RuleExpressionOperator.EQ,
    RuleExpressionOperator.NE,
    RuleExpressionOperator.GT,
    RuleExpressionOperator.GTE,
    RuleExpressionOperator.LT,
    RuleExpressionOperator.LTE,
    RuleExpressionOperator.IN,
    RuleExpressionOperator.BETWEEN,
]
_BOOLEAN_OPERATORS = [RuleExpressionOperator.EQ, RuleExpressionOperator.NE]
_ENUM_OPERATORS = [
    RuleExpressionOperator.EQ,
    RuleExpressionOperator.NE,
    RuleExpressionOperator.IN,
]


def _feature(
    field: str,
    display_name: str,
    value_type: str,
    operators: list[RuleExpressionOperator],
    *,
    allowed_values: list[str | int | bool] | None = None,
    derived: bool = False,
    source_fields: list[str] | None = None,
) -> RuleFeatureResponse:
    return RuleFeatureResponse(
        field=field,
        display_name=display_name,
        value_type=value_type,
        operators=operators,
        allowed_values=allowed_values,
        derived=derived,
        source_fields=source_fields or [],
    )


RULE_FEATURES = (
    # 날짜는 파생 연령의 입력으로만 노출한다. JSON 문자열과 datetime을 직접
    # 비교하는 룰은 엔진에서 일관되게 평가할 수 없으므로 선택 연산자를 두지 않는다.
    _feature("transaction_datetime", "거래일시", "datetime", []),
    _feature(
        "customer_loan_type",
        "대출 신청 유형",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["a", "b", "c", "d", "e"],
    ),
    _feature(
        "customer_gender",
        "고객 성별",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["male", "female"],
    ),
    _feature(
        "customer_credit_rating",
        "고객 신용등급",
        "integer",
        _NUMERIC_OPERATORS,
    ),
    _feature(
        "account_account_type",
        "계좌 유형",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["a", "b", "c", "d", "e"],
    ),
    _feature(
        "customer_flag_terminal_malicious_behavior_1",
        "전화번호 조작 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "customer_flag_terminal_malicious_behavior_2",
        "원격제어 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "customer_flag_terminal_malicious_behavior_5",
        "신뢰할 수 없는 인증서 사용 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "customer_rooting_jailbreak_indicator",
        "루팅·탈옥 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    *(
        _feature(
            f"customer_flag_change_of_authentication_{number}",
            f"인증정보 변경 플래그 {number}",
            "integer",
            _ENUM_OPERATORS,
            allowed_values=[0, 1],
        )
        for number in range(1, 5)
    ),
    _feature(
        "account_indicator_openbanking",
        "오픈뱅킹 사용 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "channel",
        "거래 채널",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["mobile", "internet", "atm", "others"],
    ),
    _feature(
        "operating_system",
        "운영체제",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["android", "ios", "windows", "macos", "linux", "others"],
    ),
    _feature(
        "recipient_release_suspension",
        "30일 이내 본인계좌 정지해제 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "recipient_account_suspend_status",
        "수취계좌 거래중지 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    *(
        _feature(
            field,
            display_name,
            "integer",
            _ENUM_OPERATORS,
            allowed_values=[0, 1],
        )
        for field, display_name in (
            ("customer_inquery_atm_limit", "ATM 한도 문의 여부"),
            ("customer_increase_atm_limit", "ATM 한도 증액 여부"),
            (
                "customer_flag_terminal_malicious_behavior_3",
                "단말 템퍼링 여부",
            ),
            (
                "customer_flag_terminal_malicious_behavior_6",
                "키로깅 탐지 여부",
            ),
            ("customer_vpn_indicator", "VPN 사용 여부"),
            ("customer_mobile_roaming_indicator", "모바일 로밍 여부"),
            (
                "account_indicator_release_limit_excess",
                "한도 초과 해제 여부",
            ),
            ("unused_account_status", "휴면계좌 여부"),
            ("another_person_account", "타인계좌 이체 여부"),
            (
                "flag_deposit_more_than_ten_million",
                "최근 7일 1천만원 이상 입금 여부",
            ),
            ("unused_terminal_status", "미사용 단말 여부"),
        )
    ),
    *(
        _feature(field, display_name, "integer", _NUMERIC_OPERATORS)
        for field, display_name in (
            ("transaction_num_connection_failure", "접속 실패 횟수"),
            ("transaction_amount", "거래 금액"),
            ("account_initial_balance", "거래 전 초기 잔액"),
            ("account_balance", "거래 후 잔액"),
            ("account_amount_daily_limit", "일일 거래 한도"),
            (
                "account_remaining_amount_daily_limit_exceeded",
                "일일 한도 잔여 금액",
            ),
            ("account_one_month_max_amount", "최근 한 달 최대 거래금액"),
            (
                "transaction_history_with_the_account",
                "수취계좌 과거 거래 횟수",
            ),
            (
                "number_of_transaction_with_the_account",
                "수취계좌 단시간 거래 횟수",
            ),
        )
    ),
    _feature(
        "account_one_month_std_dev",
        "최근 한 달 거래금액 표준편차",
        "number",
        _NUMERIC_OPERATORS,
    ),
    _feature("distance", "직전 거래와의 거리", "number", _NUMERIC_OPERATORS),
    _feature(
        "access_medium",
        "접근 매체",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=list("abcdefgh"),
    ),
    _feature(
        "type_general_automatic",
        "일반·자동 거래 구분",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["general", "automatic"],
    ),
    _feature("customer_registration_datetime", "고객 등록일시", "datetime", []),
    _feature("account_creation_datetime", "계좌 개설일시", "datetime", []),
    _feature("last_atm_transaction_datetime", "최근 ATM 거래일시", "datetime", []),
    _feature(
        "last_bank_branch_transaction_datetime",
        "최근 영업점 거래일시",
        "datetime",
        [],
    ),
    _feature(
        "account_dawn_one_month_max_amount",
        "최근 한 달 새벽 최대 거래금액",
        "number",
        _NUMERIC_OPERATORS,
    ),
    _feature(
        "account_dawn_one_month_std_dev",
        "최근 한 달 새벽 거래금액 표준편차",
        "number",
        _NUMERIC_OPERATORS,
    ),
    _feature(
        "recipient_transaction_resumed_date",
        "수취 계좌 거래 재개일",
        "datetime",
        [],
    ),
    _feature("time_difference", "직전 거래 후 경과시간", "duration", []),
    _feature(
        "transaction_age",
        "거래 시점 연령",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=["customer_birth_date", "transaction_datetime"],
    ),
    _feature(
        "authentication_change_count",
        "인증정보 변경 개수",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=[
            f"customer_flag_change_of_authentication_{number}" for number in range(1, 5)
        ],
    ),
    _feature(
        "limit_action_count",
        "한도 문의·증액·해제 충족 개수",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=[
            "customer_inquery_atm_limit",
            "customer_increase_atm_limit",
            "account_indicator_release_limit_excess",
        ],
    ),
    _feature(
        "device_compromise_count",
        "단말침해 신호 개수",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=[
            "customer_flag_terminal_malicious_behavior_3",
            "customer_flag_terminal_malicious_behavior_5",
            "customer_flag_terminal_malicious_behavior_6",
            "customer_rooting_jailbreak_indicator",
        ],
    ),
    *(
        _feature(
            field,
            display_name,
            "boolean",
            _BOOLEAN_OPERATORS,
            derived=True,
            source_fields=source_fields,
        )
        for field, display_name, source_fields in (
            (
                "strong_auth_change",
                "인증정보 변경 3개 이상",
                [
                    f"customer_flag_change_of_authentication_{number}"
                    for number in range(1, 5)
                ],
            ),
            ("loan_related", "대출 관련 여부", ["customer_loan_type"]),
            (
                "all_limit_actions",
                "한도 문의·증액·해제 3종 모두",
                [
                    "customer_inquery_atm_limit",
                    "customer_increase_atm_limit",
                    "account_indicator_release_limit_excess",
                ],
            ),
            (
                "device_compromise_2plus",
                "단말침해 신호 2개 이상",
                [
                    "customer_flag_terminal_malicious_behavior_3",
                    "customer_flag_terminal_malicious_behavior_5",
                    "customer_flag_terminal_malicious_behavior_6",
                    "customer_rooting_jailbreak_indicator",
                ],
            ),
            (
                "new_or_rare_recipient",
                "신규·희소 수취인",
                ["transaction_history_with_the_account"],
            ),
            (
                "recipient_transfer",
                "신규·희소 수취인 타계좌 이체",
                [
                    "transaction_history_with_the_account",
                    "another_person_account",
                ],
            ),
            (
                "rapid_repeat",
                "단시간 반복 이체",
                ["number_of_transaction_with_the_account"],
            ),
            (
                "amount_anomaly",
                "월간 기준 금액 이상",
                [
                    "transaction_amount",
                    "account_one_month_max_amount",
                    "account_one_month_std_dev",
                ],
            ),
            (
                "balance_depletion",
                "잔액 소진",
                [
                    "transaction_amount",
                    "account_initial_balance",
                    "account_balance",
                ],
            ),
            (
                "daily_limit_pressure",
                "일일 한도 근접",
                [
                    "transaction_amount",
                    "account_amount_daily_limit",
                    "account_remaining_amount_daily_limit_exceeded",
                ],
            ),
            (
                "severe_amount_context",
                "이상금액과 잔액·한도 압박 동시 충족",
                [
                    "transaction_amount",
                    "account_initial_balance",
                    "account_balance",
                    "account_amount_daily_limit",
                    "account_remaining_amount_daily_limit_exceeded",
                    "account_one_month_max_amount",
                    "account_one_month_std_dev",
                ],
            ),
            (
                "loan_escalation_context",
                "대출 상승 맥락",
                [
                    "customer_loan_type",
                    "customer_flag_terminal_malicious_behavior_1",
                    "customer_inquery_atm_limit",
                    "customer_increase_atm_limit",
                    "account_indicator_release_limit_excess",
                    "transaction_amount",
                    "account_initial_balance",
                    "account_balance",
                    "account_amount_daily_limit",
                    "account_remaining_amount_daily_limit_exceeded",
                    "account_one_month_max_amount",
                    "account_one_month_std_dev",
                ],
            ),
            (
                "impossible_travel",
                "불가능 이동",
                ["distance", "time_difference"],
            ),
            (
                "recently_resumed",
                "휴면계좌의 최근 거래 재개",
                [
                    "unused_account_status",
                    "transaction_datetime",
                    "recipient_transaction_resumed_date",
                ],
            ),
            (
                "phone_number_manipulation",
                "전화번호 조작",
                ["customer_flag_terminal_malicious_behavior_1"],
            ),
            (
                "remote_control",
                "원격제어",
                ["customer_flag_terminal_malicious_behavior_2"],
            ),
            (
                "vulnerable_mobile",
                "60세 이상 모바일 환경",
                [
                    "customer_birth_date",
                    "transaction_datetime",
                    "channel",
                    "operating_system",
                ],
            ),
            (
                "account_suspension_released",
                "본인계좌 최근 정지해제",
                ["recipient_release_suspension"],
            ),
            (
                "recipient_account_suspended",
                "수취계좌 거래중지",
                ["recipient_account_suspend_status"],
            ),
            (
                "suspension_pair",
                "정지해제·수취정지 동시 충족",
                [
                    "recipient_release_suspension",
                    "recipient_account_suspend_status",
                ],
            ),
            (
                "suspension_release_only",
                "정지해제만 충족",
                [
                    "recipient_release_suspension",
                    "recipient_account_suspend_status",
                ],
            ),
            (
                "recipient_suspended_only",
                "수취정지만 충족",
                [
                    "recipient_release_suspension",
                    "recipient_account_suspend_status",
                ],
            ),
            (
                "vpn_or_roaming",
                "VPN 또는 로밍",
                ["customer_vpn_indicator", "customer_mobile_roaming_indicator"],
            ),
        )
    ),
)
_FEATURE_BY_FIELD = {feature.field: feature for feature in RULE_FEATURES}


def _not_found(subject: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{subject}을(를) 찾을 수 없습니다.",
    )


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _get_rule_set(session: Session, rule_set_id: int) -> FraudRuleSet:
    rule_set = session.get(FraudRuleSet, rule_set_id)
    if rule_set is None:
        raise _not_found("룰셋")
    return rule_set


def _get_rule(session: Session, rule_set_id: int, rule_id: int) -> FraudRule:
    rule = session.get(FraudRule, rule_id)
    if rule is None or rule.rule_set_id != rule_set_id:
        raise _not_found("룰")
    return rule


def _assert_draft(rule_set: FraudRuleSet) -> None:
    if rule_set.status != FraudRuleSetStatus.DRAFT:
        raise _conflict("DRAFT 룰셋만 수정할 수 있습니다.")


def _components_for_rule(session: Session, rule_id: int) -> list[FraudRuleComponent]:
    statement = (
        select(FraudRuleComponent)
        .where(FraudRuleComponent.rule_id == rule_id)
        .order_by(FraudRuleComponent.sort_order, FraudRuleComponent.id)
    )
    return list(session.exec(statement).all())


def _rules_for_set(session: Session, rule_set_id: int) -> list[FraudRule]:
    statement = (
        select(FraudRule)
        .where(FraudRule.rule_set_id == rule_set_id)
        .order_by(FraudRule.sort_order, FraudRule.id)
    )
    return list(session.exec(statement).all())


def _component_response(component: FraudRuleComponent) -> FraudRuleComponentResponse:
    return FraudRuleComponentResponse.model_validate(component)


def _rule_response(session: Session, rule: FraudRule) -> FraudRuleResponse:
    return FraudRuleResponse(
        id=rule.id,
        type_code=rule.type_code,
        display_name=rule.display_name,
        description=rule.description,
        enabled=rule.enabled,
        sort_order=rule.sort_order,
        components=[
            _component_response(component)
            for component in _components_for_rule(session, rule.id)
        ],
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _rule_set_response(
    session: Session, rule_set: FraudRuleSet
) -> FraudRuleSetResponse:
    return FraudRuleSetResponse(
        id=rule_set.id,
        version=rule_set.version,
        status=rule_set.status,
        created_at=rule_set.created_at,
        updated_at=rule_set.updated_at,
        activated_at=rule_set.activated_at,
        rules=[
            _rule_response(session, rule)
            for rule in _rules_for_set(session, rule_set.id)
        ],
    )


def _add_component(
    session: Session,
    rule: FraudRule,
    component: FraudRuleComponentCreate,
) -> FraudRuleComponent:
    persisted = FraudRuleComponent(
        rule_id=rule.id,
        component_key=component.component_key,
        name=component.name,
        condition_expression=expression_to_json(component.condition_expression),
        weight=component.weight,
        sort_order=component.sort_order,
    )
    session.add(persisted)
    return persisted


def _add_rule(
    session: Session,
    rule_set: FraudRuleSet,
    payload: FraudRuleCreate,
) -> FraudRule:
    rule = FraudRule(
        rule_set_id=rule_set.id,
        type_code=payload.type_code,
        display_name=payload.display_name,
        description=payload.description,
        enabled=payload.enabled,
        sort_order=payload.sort_order,
    )
    session.add(rule)
    session.flush()
    for component in payload.components:
        _add_component(session, rule, component)
    return rule


def _expression_type_issues(
    expression: Mapping[str, Any],
    path: str,
) -> Iterable[FraudRuleValidationIssue]:
    operator = expression.get("operator")
    if operator in {"AND", "OR"}:
        for index, condition in enumerate(expression.get("conditions", [])):
            if isinstance(condition, Mapping):
                yield from _expression_type_issues(
                    condition,
                    f"{path}.conditions[{index}]",
                )
        return

    field = expression.get("field")
    feature = _FEATURE_BY_FIELD.get(field)
    if feature is None:
        if field in TRANSITION_LEGACY_RAW_ALIASES or field in (
            TRANSITION_LEGACY_DERIVED_FEATURES
        ):
            yield FraudRuleValidationIssue(
                path=f"{path}.field",
                message=(
                    f"{field}은 기존 ACTIVE 룰 평가 전용 이름입니다. "
                    "새 룰에는 raw51 snake_case 필드를 사용해야 합니다."
                ),
            )
        return
    allowed = {item.value for item in feature.operators}
    if operator not in allowed:
        yield FraudRuleValidationIssue(
            path=f"{path}.operator",
            message=f"{field}에는 {operator} 연산자를 사용할 수 없습니다.",
        )
        return

    expected = expression.get("value")
    values = expected if operator in {"IN", "BETWEEN"} else [expected]
    if not isinstance(values, list):
        return

    if feature.value_type == "boolean" and any(
        not isinstance(value, bool) for value in values
    ):
        yield FraudRuleValidationIssue(
            path=f"{path}.value",
            message=f"{field}의 비교값은 boolean이어야 합니다.",
        )
    elif feature.value_type == "integer" and any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        yield FraudRuleValidationIssue(
            path=f"{path}.value",
            message=f"{field}의 비교값은 정수여야 합니다.",
        )
    elif feature.value_type == "number" and any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        yield FraudRuleValidationIssue(
            path=f"{path}.value",
            message=f"{field}의 비교값은 숫자여야 합니다.",
        )
    elif feature.value_type == "enum":
        allowed_values = set(feature.allowed_values or [])
        invalid = [value for value in values if value not in allowed_values]
        if invalid:
            yield FraudRuleValidationIssue(
                path=f"{path}.value",
                message=f"{field}에 허용되지 않은 값입니다: {invalid}",
            )

    if operator == "BETWEEN" and len(values) == 2:
        try:
            reversed_range = values[0] > values[1]
        except TypeError:
            reversed_range = False
        if reversed_range:
            yield FraudRuleValidationIssue(
                path=f"{path}.value",
                message="BETWEEN의 최솟값은 최댓값보다 클 수 없습니다.",
            )


def _definition_validation_issues(
    definition: RuleSetDefinition,
) -> list[FraudRuleValidationIssue]:
    issues: list[FraudRuleValidationIssue] = []
    try:
        RuleEngine().validate_rule_set(definition)
    except (RuleSetValidationError, RuleExpressionError) as exc:
        issues.append(FraudRuleValidationIssue(path="rule_set", message=str(exc)))

    for rule_index, rule in enumerate(definition.rules):
        if not rule.enabled:
            continue
        for component_index, component in enumerate(rule.components):
            issues.extend(
                _expression_type_issues(
                    component.condition_expression,
                    f"rules[{rule_index}].components[{component_index}].condition_expression",
                )
            )
    return issues


def _validation_issues(
    session: Session,
    rule_set: FraudRuleSet,
) -> list[FraudRuleValidationIssue]:
    return _definition_validation_issues(
        rule_set_definition_from_database(session, rule_set)
    )


def _copy_definition_rules(
    session: Session,
    target: FraudRuleSet,
    source: RuleSetDefinition,
) -> None:
    for rule_order, rule in enumerate(source.rules):
        payload = FraudRuleCreate(
            type_code=rule.type_code,
            display_name=rule.display_name,
            enabled=rule.enabled,
            sort_order=rule_order,
            components=[
                FraudRuleComponentCreate(
                    component_key=component.component_key,
                    name=component.name,
                    condition_expression=component.condition_expression,
                    weight=component.weight,
                    sort_order=component_order,
                )
                for component_order, component in enumerate(rule.components)
            ],
        )
        _add_rule(session, target, payload)


def _copy_persisted_rules(
    session: Session,
    target: FraudRuleSet,
    source: FraudRuleSet,
) -> None:
    for rule in _rules_for_set(session, source.id):
        payload = FraudRuleCreate(
            type_code=rule.type_code,
            display_name=rule.display_name,
            description=rule.description,
            enabled=rule.enabled,
            sort_order=rule.sort_order,
            components=[
                FraudRuleComponentCreate(
                    component_key=component.component_key,
                    name=component.name,
                    condition_expression=component.condition_expression,
                    weight=component.weight,
                    sort_order=component.sort_order,
                )
                for component in _components_for_rule(session, rule.id)
            ],
        )
        _add_rule(session, target, payload)


def _commit_or_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict(message) from exc


@router.get("/rule-features", response_model=list[RuleFeatureResponse])
def list_rule_features() -> list[RuleFeatureResponse]:
    """Return only the allow-listed raw and derived fields usable by rules."""

    return list(RULE_FEATURES)


@router.get(
    "/rule-sets",
    response_model=list[FraudRuleSetSummaryResponse],
)
def list_rule_sets(
    session: SessionDep,
    rule_set_status: FraudRuleSetStatus | None = None,
) -> list[FraudRuleSet]:
    statement = select(FraudRuleSet)
    if rule_set_status is not None:
        statement = statement.where(FraudRuleSet.status == rule_set_status)
    statement = statement.order_by(FraudRuleSet.version.desc())
    return list(session.exec(statement).all())


@router.get("/rule-sets/active", response_model=FraudRuleSetResponse)
def get_active_rule_set(session: SessionDep) -> FraudRuleSetResponse:
    statement = (
        select(FraudRuleSet)
        .where(FraudRuleSet.status == FraudRuleSetStatus.ACTIVE)
        .order_by(FraudRuleSet.version.desc())
    )
    rule_set = session.exec(statement).first()
    if rule_set is None:
        raise _not_found("활성 룰셋")
    return _rule_set_response(session, rule_set)


@router.get("/rule-sets/{rule_set_id}", response_model=FraudRuleSetResponse)
def get_rule_set(rule_set_id: int, session: SessionDep) -> FraudRuleSetResponse:
    return _rule_set_response(session, _get_rule_set(session, rule_set_id))


@router.post(
    "/rule-sets/drafts",
    response_model=FraudRuleSetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_draft_rule_set(
    session: SessionDep,
    payload: FraudRuleSetDraftCreate | None = None,
) -> FraudRuleSetResponse:
    existing_draft = session.exec(
        select(FraudRuleSet)
        .where(FraudRuleSet.status == FraudRuleSetStatus.DRAFT)
        .order_by(FraudRuleSet.version.desc())
    ).first()
    if existing_draft is not None:
        raise _conflict(f"이미 수정 중인 DRAFT 룰셋이 있습니다: {existing_draft.id}")

    source: FraudRuleSet | None
    if payload is not None and payload.source_rule_set_id is not None:
        source = _get_rule_set(session, payload.source_rule_set_id)
    else:
        source = session.exec(
            select(FraudRuleSet)
            .where(FraudRuleSet.status == FraudRuleSetStatus.ACTIVE)
            .order_by(FraudRuleSet.version.desc())
        ).first()

    maximum_version = session.exec(select(func.max(FraudRuleSet.version))).one()
    version = (maximum_version or 0) + 1
    draft = FraudRuleSet(
        version=version,
        status=FraudRuleSetStatus.DRAFT,
    )
    session.add(draft)
    session.flush()

    if source is None:
        _copy_definition_rules(session, draft, DEFAULT_RULE_SET)
    else:
        _copy_persisted_rules(session, draft, source)

    _commit_or_conflict(session, "동일한 룰셋 버전이 이미 생성되었습니다.")
    session.refresh(draft)
    return _rule_set_response(session, draft)


@router.delete(
    "/rule-sets/{rule_set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_draft_rule_set(
    rule_set_id: int,
    session: SessionDep,
) -> Response:
    """저장하지 않을 DRAFT와 그 하위 룰·조건을 함께 폐기한다."""

    rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(rule_set)
    for rule in _rules_for_set(session, rule_set.id):
        for component in _components_for_rule(session, rule.id):
            session.delete(component)
        session.flush()
        session.delete(rule)
    session.flush()
    session.delete(rule_set)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/rule-sets/{rule_set_id}/rules",
    response_model=FraudRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rule(
    rule_set_id: int,
    payload: FraudRuleCreate,
    session: SessionDep,
) -> FraudRuleResponse:
    rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(rule_set)
    duplicate = session.exec(
        select(FraudRule).where(
            FraudRule.rule_set_id == rule_set_id,
            FraudRule.type_code == payload.type_code,
        )
    ).first()
    if duplicate is not None:
        raise _conflict("같은 룰셋에 동일한 type_code를 추가할 수 없습니다.")

    rule = _add_rule(session, rule_set, payload)
    rule_set.updated_at = datetime.now()
    session.add(rule_set)
    _commit_or_conflict(session, "룰 또는 component_key가 중복되었습니다.")
    session.refresh(rule)
    return _rule_response(session, rule)


@router.put(
    "/rule-sets/{rule_set_id}/rules/{rule_id}",
    response_model=FraudRuleResponse,
)
def update_rule(
    rule_set_id: int,
    rule_id: int,
    payload: FraudRuleUpdate,
    session: SessionDep,
) -> FraudRuleResponse:
    rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(rule_set)
    rule = _get_rule(session, rule_set_id, rule_id)

    scalar_fields = {
        "type_code",
        "display_name",
        "description",
        "enabled",
        "sort_order",
    }
    for field_name in payload.model_fields_set & scalar_fields:
        setattr(rule, field_name, getattr(payload, field_name))

    now = datetime.now()
    rule.updated_at = now
    rule_set.updated_at = now
    session.add(rule)
    session.add(rule_set)

    if payload.components is not None:
        for component in _components_for_rule(session, rule.id):
            session.delete(component)
        session.flush()
        for component in payload.components:
            _add_component(session, rule, component)

    _commit_or_conflict(session, "룰 또는 component_key가 중복되었습니다.")
    session.refresh(rule)
    return _rule_response(session, rule)


@router.delete(
    "/rule-sets/{rule_set_id}/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_rule(
    rule_set_id: int,
    rule_id: int,
    session: SessionDep,
) -> Response:
    rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(rule_set)
    rule = _get_rule(session, rule_set_id, rule_id)
    for component in _components_for_rule(session, rule.id):
        session.delete(component)
    session.flush()
    session.delete(rule)
    rule_set.updated_at = datetime.now()
    session.add(rule_set)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/rule-sets/{rule_set_id}/validate",
    response_model=FraudRuleValidationResponse,
)
def validate_rule_set(
    rule_set_id: int,
    session: SessionDep,
) -> FraudRuleValidationResponse:
    rule_set = _get_rule_set(session, rule_set_id)
    issues = _validation_issues(session, rule_set)
    return FraudRuleValidationResponse(
        rule_set_id=rule_set.id,
        valid=not issues,
        issues=issues,
    )


@router.post(
    "/rule-sets/{rule_set_id}/replay",
    response_model=FraudRuleReplayResponse,
)
def replay_draft_rule_set(
    rule_set_id: int,
    session: SessionDep,
    payload: FraudRuleReplayRequest | None = None,
) -> FraudRuleReplayResponse:
    """최신 ML 양성 거래에 ACTIVE와 DRAFT를 적용해 영향만 비교한다.

    거래, ML 예측, 기존 운영 룰 점수는 생성·수정하지 않는다. 실행 시작 후
    ACTIVE/DRAFT 상태나 갱신 시각이 달라지면 혼합 결과를 반환하지 않고 409로
    중단한다.
    """

    draft_rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(draft_rule_set)
    draft_definition = rule_set_definition_from_database(session, draft_rule_set)
    draft_issues = _definition_validation_issues(draft_definition)
    if draft_issues:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "유효하지 않은 DRAFT 룰셋은 리플레이할 수 없습니다.",
                "issues": [issue.model_dump() for issue in draft_issues],
            },
        )

    active = rule_repository.get_active_rule_set(session)
    if active is None:
        raise _conflict("ACTIVE 룰셋이 없어 DRAFT와 비교할 수 없습니다.")
    active_rule_set, active_definition = active
    if active_rule_set.id == draft_rule_set.id:
        raise _conflict("DRAFT가 이미 ACTIVE로 변경되어 다시 조회해야 합니다.")
    active_issues = _definition_validation_issues(active_definition)
    if active_issues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "현재 ACTIVE 룰셋이 유효하지 않아 비교할 수 없습니다.",
                "issues": [issue.model_dump() for issue in active_issues],
            },
        )

    snapshots = {
        draft_rule_set.id: (
            draft_rule_set.status,
            draft_rule_set.updated_at,
        ),
        active_rule_set.id: (
            active_rule_set.status,
            active_rule_set.updated_at,
        ),
    }
    request = payload or FraudRuleReplayRequest()
    result = replay_rule_sets(
        session=session,
        active_definition=active_definition,
        draft_definition=draft_definition,
        sample_size=request.sample_size,
        detail_limit=request.detail_limit,
    )

    # 긴 리플레이 중 관리자가 룰셋을 수정·활성화했으면 시작 시점 정의와 현재
    # 상태가 섞인 결과가 된다. 쓰기 잠금을 잡지 않고 최신 상태만 다시 확인한다.
    session.expire_all()
    for snapshot_id, (snapshot_status, snapshot_updated_at) in snapshots.items():
        current = session.get(FraudRuleSet, snapshot_id)
        if (
            current is None
            or current.status != snapshot_status
            or current.updated_at != snapshot_updated_at
        ):
            raise _conflict(
                "리플레이 도중 룰셋이 변경되었습니다. 최신 상태로 다시 실행하세요."
            )

    return FraudRuleReplayResponse(
        active_rule_set=FraudRuleReplayRuleSetResponse(
            rule_set_id=active_rule_set.id,
            version=active_rule_set.version,
            updated_at=active_rule_set.updated_at,
        ),
        draft_rule_set=FraudRuleReplayRuleSetResponse(
            rule_set_id=draft_rule_set.id,
            version=draft_rule_set.version,
            updated_at=draft_rule_set.updated_at,
        ),
        requested_count=result.requested_count,
        selected_count=result.selected_count,
        evaluated_count=result.evaluated_count,
        error_count=result.error_count,
        summary_denominator=result.evaluated_count,
        detail_limit=request.detail_limit,
        has_more=result.has_more,
        changed_transaction_count=result.changed_transaction_count,
        changed_transaction_rate=result.changed_transaction_rate,
        score_changed_transaction_count=result.score_changed_transaction_count,
        evidence_changed_transaction_count=result.evidence_changed_transaction_count,
        active_no_match_count=result.active_no_match_count,
        draft_no_match_count=result.draft_no_match_count,
        no_match_count_delta=result.no_match_count_delta,
        type_summaries=[asdict(item) for item in result.type_summaries],
        component_impacts=[asdict(item) for item in result.component_impacts],
        changed_transaction_details=[
            asdict(item) for item in result.changed_transaction_details
        ],
        error_details=[asdict(item) for item in result.error_details],
        changed_details_truncated=result.changed_details_truncated,
        error_details_truncated=result.error_details_truncated,
    )


@router.post(
    "/rule-sets/{rule_set_id}/activate",
    response_model=FraudRuleSetResponse,
)
def activate_rule_set(
    rule_set_id: int,
    session: SessionDep,
) -> FraudRuleSetResponse:
    statement = (
        select(FraudRuleSet).where(FraudRuleSet.id == rule_set_id).with_for_update()
    )
    rule_set = session.exec(statement).first()
    if rule_set is None:
        raise _not_found("룰셋")
    _assert_draft(rule_set)

    issues = _validation_issues(session, rule_set)
    if issues:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "검증을 통과하지 못한 룰셋은 활성화할 수 없습니다.",
                "issues": [issue.model_dump() for issue in issues],
            },
        )

    now = datetime.now()
    active_sets = session.exec(
        select(FraudRuleSet)
        .where(FraudRuleSet.status == FraudRuleSetStatus.ACTIVE)
        .with_for_update()
    ).all()
    for active in active_sets:
        active.status = FraudRuleSetStatus.ARCHIVED
        active.updated_at = now
        session.add(active)

    rule_set.status = FraudRuleSetStatus.ACTIVE
    rule_set.activated_at = now
    rule_set.updated_at = now
    session.add(rule_set)
    session.commit()
    session.refresh(rule_set)
    return _rule_set_response(session, rule_set)


__all__ = ["RULE_FEATURES", "router"]
