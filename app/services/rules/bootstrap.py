"""빈 DB에 최초 기본 룰셋을 저장한다."""

from datetime import datetime

from sqlmodel import Session, select

from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
)
from app.services.rules.defaults import DEFAULT_RULE_SET


def initialize_default_rule_set(session: Session) -> bool:
    """룰셋이 하나도 없을 때만 기본 룰셋을 ACTIVE 상태로 저장한다."""

    if session.exec(select(FraudRuleSet.id).limit(1)).first() is not None:
        return False

    rule_set = FraudRuleSet(
        version=1,
        status=FraudRuleSetStatus.ACTIVE,
        activated_at=datetime.now(),
    )
    session.add(rule_set)
    session.flush()

    for rule_order, definition in enumerate(DEFAULT_RULE_SET.rules):
        rule = FraudRule(
            rule_set_id=rule_set.id,
            type_code=definition.type_code,
            display_name=definition.display_name,
            enabled=definition.enabled,
            sort_order=rule_order,
        )
        session.add(rule)
        session.flush()

        for component_order, component in enumerate(definition.components):
            session.add(
                FraudRuleComponent(
                    rule_id=rule.id,
                    component_key=component.component_key,
                    name=component.name,
                    condition_expression=dict(component.condition_expression),
                    weight=component.weight,
                    sort_order=component_order,
                )
            )

    session.commit()
    return True


__all__ = ["initialize_default_rule_set"]
