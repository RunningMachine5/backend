import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
)
from app.services.rules.bootstrap import initialize_default_rule_set
from app.services.rules.defaults import DEFAULT_RULE_SET
from app.services.rules.repository import rule_set_definition_from_database


class RuleBootstrapTest(unittest.TestCase):
    def test_stores_default_active_rule_set_only_once(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        FraudRuleSet.__table__.create(engine)
        FraudRule.__table__.create(engine)
        FraudRuleComponent.__table__.create(engine)

        with Session(engine) as session:
            self.assertTrue(initialize_default_rule_set(session))
            self.assertFalse(initialize_default_rule_set(session))

            stored = session.exec(select(FraudRuleSet)).one()
            definition = rule_set_definition_from_database(session, stored)

            self.assertEqual(stored.version, 1)
            self.assertEqual(stored.status, FraudRuleSetStatus.ACTIVE)
            self.assertIsNotNone(stored.activated_at)
            self.assertEqual(definition.rules, DEFAULT_RULE_SET.rules)

        engine.dispose()


if __name__ == "__main__":
    unittest.main()
