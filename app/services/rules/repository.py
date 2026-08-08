"""DB에 저장된 룰셋을 순수 룰 엔진 정의로 변환한다."""

from __future__ import annotations

from sqlmodel import Session, select

from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
)
from app.services.rules.engine import (
    FraudRuleDefinition,
    RuleComponentDefinition,
    RuleSetDefinition,
)


def rule_set_definition_from_database(
    session: Session,
    rule_set: FraudRuleSet,
) -> RuleSetDefinition:
    """정렬된 DB 행을 실행 가능한 불변 룰 정의로 변환한다."""

    rules = session.exec(
        select(FraudRule)
        .where(FraudRule.rule_set_id == rule_set.id)
        .order_by(FraudRule.sort_order, FraudRule.id)
    ).all()

    definitions: list[FraudRuleDefinition] = []
    for rule in rules:
        components = session.exec(
            select(FraudRuleComponent)
            .where(FraudRuleComponent.rule_id == rule.id)
            .order_by(FraudRuleComponent.sort_order, FraudRuleComponent.id)
        ).all()
        definitions.append(
            FraudRuleDefinition(
                type_code=rule.type_code,
                display_name=rule.display_name,
                enabled=rule.enabled,
                components=tuple(
                    RuleComponentDefinition(
                        component_key=component.component_key,
                        name=component.name,
                        condition_expression=component.condition_expression,
                        weight=component.weight,
                    )
                    for component in components
                ),
            )
        )

    return RuleSetDefinition(
        version=f"v{rule_set.version}",
        rules=tuple(definitions),
        minimum_score=rule_set.minimum_score,
        ambiguity_margin=rule_set.ambiguity_margin,
    )


def get_active_rule_set(
    session: Session,
) -> tuple[FraudRuleSet, RuleSetDefinition] | None:
    """현재 ACTIVE 룰셋과 엔진 정의를 함께 반환한다."""

    rule_set = session.exec(
        select(FraudRuleSet)
        .where(FraudRuleSet.status == FraudRuleSetStatus.ACTIVE)
        .order_by(FraudRuleSet.version.desc())
    ).first()
    if rule_set is None:
        return None
    return rule_set, rule_set_definition_from_database(session, rule_set)


__all__ = ["get_active_rule_set", "rule_set_definition_from_database"]
