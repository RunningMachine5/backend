"""Administrator API for versioned fraud-type rule management."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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
    FraudRuleResponse,
    FraudRuleSetDraftCreate,
    FraudRuleSetResponse,
    FraudRuleSetSummaryResponse,
    FraudRuleSetUpdate,
    FraudRuleTestRequest,
    FraudRuleTestResponse,
    FraudRuleTypeScoreResponse,
    FraudRuleUpdate,
    FraudRuleValidationIssue,
    FraudRuleValidationResponse,
    RuleExpressionOperator,
    RuleFeatureResponse,
    expression_to_json,
)
from app.services.rules.engine import (
    RuleEngine,
    RuleSetValidationError,
)
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.feature_builder import RuleFeatureError
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
    _feature("Customer_Birthyear", "고객 출생연도", "integer", _NUMERIC_OPERATORS),
    # 날짜는 파생 연령의 입력으로만 노출한다. JSON 문자열과 datetime을 직접
    # 비교하는 룰은 엔진에서 일관되게 평가할 수 없으므로 선택 연산자를 두지 않는다.
    _feature("Transaction_Datetime", "거래일시", "datetime", []),
    _feature(
        "Customer_loan_type",
        "대출 신청 유형",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["a", "b", "c", "d", "e"],
    ),
    _feature(
        "Customer_flag_terminal_malicious_behavior_1",
        "전화번호 조작 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "Customer_flag_terminal_malicious_behavior_2",
        "원격제어 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "Customer_flag_terminal_malicious_behavior_5",
        "신뢰할 수 없는 인증서 사용 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "Customer_rooting_jailbreak_indicator",
        "루팅·탈옥 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    *(
        _feature(
            f"Customer_flag_change_of_authentication_{number}",
            f"인증정보 변경 플래그 {number}",
            "integer",
            _ENUM_OPERATORS,
            allowed_values=[0, 1],
        )
        for number in range(1, 5)
    ),
    _feature(
        "Account_indicator_Openbanking",
        "오픈뱅킹 사용 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "Channel",
        "거래 채널",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["mobile", "internet", "ATM", "Others"],
    ),
    _feature(
        "Operating_System",
        "운영체제",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["Android", "iOS", "Windows", "macOS", "Linux", "Others"],
    ),
    _feature(
        "Account_release_suspension",
        "30일 이내 본인계좌 정지해제 여부",
        "integer",
        _ENUM_OPERATORS,
        allowed_values=[0, 1],
    ),
    _feature(
        "Recipient_account_suspend_status",
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
            ("Customer_inquery_atm_limit", "ATM 한도 문의 여부"),
            ("Customer_increase_atm_limit", "ATM 한도 증액 여부"),
            (
                "Customer_flag_terminal_malicious_behavior_3",
                "단말 템퍼링 여부",
            ),
            (
                "Customer_flag_terminal_malicious_behavior_6",
                "키로깅 탐지 여부",
            ),
            ("Customer_VPN_Indicator", "VPN 사용 여부"),
            ("Customer_mobile_roaming_indicator", "모바일 로밍 여부"),
            (
                "Account_indicator_release_limit_excess",
                "한도 초과 해제 여부",
            ),
            ("Unused_account_status", "휴면계좌 여부"),
            ("Another_Person_Account", "타인계좌 이체 여부"),
            (
                "Flag_deposit_more_than_tenMillion",
                "최근 7일 1천만원 이상 입금 여부",
            ),
            ("Unused_terminal_status", "미사용 단말 여부"),
            (
                "First_time_iOS_by_vulnerable_user",
                "취약고객의 60세 이후 iOS 첫 사용 여부",
            ),
        )
    ),
    *(
        _feature(field, display_name, "integer", _NUMERIC_OPERATORS)
        for field, display_name in (
            ("Transaction_num_connection_failure", "접속 실패 횟수"),
            ("Transaction_Amount", "거래 금액"),
            ("Account_initial_balance", "거래 전 초기 잔액"),
            ("Account_balance", "거래 후 잔액"),
            ("Account_amount_daily_limit", "일일 거래 한도"),
            (
                "Account_remaining_amount_daily_limit_exceeded",
                "일일 한도 잔여 금액",
            ),
            ("Account_one_month_max_amount", "최근 한 달 최대 거래금액"),
            (
                "Transaction_history_with_the_account",
                "수취계좌 과거 거래 횟수",
            ),
            (
                "Number_of_transaction_with_the_account",
                "수취계좌 단시간 거래 횟수",
            ),
        )
    ),
    _feature(
        "Account_one_month_std_dev",
        "최근 한 달 거래금액 표준편차",
        "number",
        _NUMERIC_OPERATORS,
    ),
    _feature("Distance", "직전 거래와의 거리", "number", _NUMERIC_OPERATORS),
    _feature(
        "Access_Medium",
        "접근 매체",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=list("abcdefgh"),
    ),
    _feature(
        "Type_General_Automatic",
        "일반·자동 거래 구분",
        "enum",
        _ENUM_OPERATORS,
        allowed_values=["general", "automatic"],
    ),
    _feature("Transaction_resumed_date", "거래 재개일", "datetime", []),
    _feature("Time Difference", "직전 거래 후 경과시간", "duration", []),
    _feature(
        "transaction_age",
        "거래 시점 연령",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=["Customer_Birthyear", "Transaction_Datetime"],
    ),
    _feature(
        "device_compromise_count",
        "단말침해 신호 개수",
        "integer",
        _NUMERIC_OPERATORS,
        derived=True,
        source_fields=[
            "Customer_flag_terminal_malicious_behavior_3",
            "Customer_flag_terminal_malicious_behavior_5",
            "Customer_flag_terminal_malicious_behavior_6",
            "Customer_rooting_jailbreak_indicator",
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
                "authentication_changed",
                "인증정보 변경 기록 있음",
                [f"Customer_flag_change_of_authentication_{number}" for number in range(1, 5)],
            ),
            ("loan_related", "대출 관련 여부", ["Customer_loan_type"]),
            (
                "new_or_rare_recipient",
                "신규·희소 수취인",
                ["Transaction_history_with_the_account"],
            ),
            (
                "rapid_repeat",
                "단시간 반복 이체",
                ["Number_of_transaction_with_the_account"],
            ),
            (
                "amount_anomaly",
                "월간 기준 금액 이상",
                [
                    "Transaction_Amount",
                    "Account_one_month_max_amount",
                    "Account_one_month_std_dev",
                ],
            ),
            (
                "impossible_travel",
                "불가능 이동",
                ["Distance", "Time Difference"],
            ),
            (
                "recently_resumed",
                "휴면계좌의 최근 거래 재개",
                [
                    "Unused_account_status",
                    "Transaction_Datetime",
                    "Transaction_resumed_date",
                ],
            ),
            (
                "card_context_proxy",
                "카드거래 맥락 대용 지표",
                ["Channel", "Another_Person_Account", "Customer_loan_type"],
            ),
            (
                "phone_number_manipulation",
                "전화번호 조작",
                ["Customer_flag_terminal_malicious_behavior_1"],
            ),
            (
                "remote_control",
                "원격제어",
                ["Customer_flag_terminal_malicious_behavior_2"],
            ),
            (
                "limit_adjustment_detected",
                "한도 문의·증액·해제 정황",
                [
                    "Customer_inquery_atm_limit",
                    "Customer_increase_atm_limit",
                    "Account_indicator_release_limit_excess",
                ],
            ),
            (
                "high_value_or_balance_pressure",
                "금액 이상·잔액 소진·일 한도 근접",
                [
                    "Transaction_Amount",
                    "Account_initial_balance",
                    "Account_balance",
                    "Account_amount_daily_limit",
                    "Account_remaining_amount_daily_limit_exceeded",
                    "Account_one_month_max_amount",
                    "Account_one_month_std_dev",
                ],
            ),
            (
                "vulnerable_mobile_environment",
                "고령자 모바일·취약 iOS 환경",
                [
                    "Customer_Birthyear",
                    "Transaction_Datetime",
                    "Channel",
                    "Operating_System",
                    "First_time_iOS_by_vulnerable_user",
                ],
            ),
            (
                "new_recipient_transfer",
                "신규·희소 수취인 타계좌 이체",
                [
                    "Transaction_history_with_the_account",
                    "Another_Person_Account",
                ],
            ),
            (
                "account_suspension_released",
                "본인계좌 최근 정지해제",
                ["Account_release_suspension"],
            ),
            (
                "recipient_account_suspended",
                "수취계좌 거래중지",
                ["Recipient_account_suspend_status"],
            ),
            (
                "vpn_or_roaming",
                "VPN 또는 로밍",
                ["Customer_VPN_Indicator", "Customer_mobile_roaming_indicator"],
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


def _rule_set_response(session: Session, rule_set: FraudRuleSet) -> FraudRuleSetResponse:
    return FraudRuleSetResponse(
        id=rule_set.id,
        version=rule_set.version,
        status=rule_set.status,
        minimum_score=rule_set.minimum_score,
        ambiguity_margin=rule_set.ambiguity_margin,
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


def _validation_issues(
    session: Session,
    rule_set: FraudRuleSet,
) -> list[FraudRuleValidationIssue]:
    definition = rule_set_definition_from_database(session, rule_set)
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
    if source is None:
        from app.services.rules.defaults import DEFAULT_RULE_SET

        minimum_score = DEFAULT_RULE_SET.minimum_score
        ambiguity_margin = DEFAULT_RULE_SET.ambiguity_margin
    else:
        minimum_score = source.minimum_score
        ambiguity_margin = source.ambiguity_margin

    draft = FraudRuleSet(
        version=version,
        status=FraudRuleSetStatus.DRAFT,
        minimum_score=minimum_score,
        ambiguity_margin=ambiguity_margin,
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


@router.put("/rule-sets/{rule_set_id}", response_model=FraudRuleSetResponse)
def update_rule_set(
    rule_set_id: int,
    payload: FraudRuleSetUpdate,
    session: SessionDep,
) -> FraudRuleSetResponse:
    rule_set = _get_rule_set(session, rule_set_id)
    _assert_draft(rule_set)
    for field_name in payload.model_fields_set:
        setattr(rule_set, field_name, getattr(payload, field_name))
    rule_set.updated_at = datetime.now()
    session.add(rule_set)
    session.commit()
    session.refresh(rule_set)
    return _rule_set_response(session, rule_set)


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
    "/rule-sets/{rule_set_id}/test",
    response_model=FraudRuleTestResponse,
)
def test_rule_set(
    rule_set_id: int,
    payload: FraudRuleTestRequest,
    session: SessionDep,
) -> FraudRuleTestResponse:
    rule_set = _get_rule_set(session, rule_set_id)
    issues = _validation_issues(session, rule_set)
    if issues:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "유효하지 않은 룰셋은 테스트할 수 없습니다.",
                "issues": [issue.model_dump() for issue in issues],
            },
        )

    definition = rule_set_definition_from_database(session, rule_set)
    try:
        result = RuleEngine().classify(
            payload.raw_data.model_dump(mode="python", by_alias=True),
            definition,
        )
    except (RuleSetValidationError, RuleExpressionError, RuleFeatureError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    display_names = {
        rule.type_code: rule.display_name
        for rule in definition.rules
        if rule.enabled
    }
    return FraudRuleTestResponse(
        status=result.status,
        fraud_type=result.fraud_type,
        decision_reason=result.decision_reason,
        top_score=result.top_score,
        second_score=result.second_score,
        score_gap=result.score_gap,
        rule_set_version=rule_set.version,
        type_scores=[
            FraudRuleTypeScoreResponse(
                type_code=type_code,
                display_name=display_names[type_code],
                score=score,
                matched_components=result.matched_components[type_code],
            )
            for type_code, score in result.type_scores.items()
        ],
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
        select(FraudRuleSet)
        .where(FraudRuleSet.id == rule_set_id)
        .with_for_update()
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
