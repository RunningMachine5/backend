import unittest

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.account import Account
from app.data.model.agent import AgentCase, AgentReview
from app.data.model.customer import Customer
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult
from app.data.model.transaction import Transaction
from app.domain.agent_status import RuleFilterStatus
from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.dto.agent import FraudTypeScoreResultDTO, RuleEvidenceDTO
from app.repositories.agent_investigation import AgentInvestigationRepository
from app.scripts.seed_agent_similar_cases import seed_agent_similar_cases
from app.services.agent.dashboard_similar_cases import DashboardSimilarCaseService
from app.services.agent.similar_case_investigator import DatabaseSimilarCaseTools


FRAUD_TYPES = (
    VOICE_PHISHING,
    MESSENGER_PHISHING,
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
)


class AgentSimilarCaseSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        for model in (
            Customer,
            Account,
            Transaction,
            MLPredictionResult,
            FraudRuleSet,
            FraudRule,
            FraudRuleComponent,
            FraudTypeScoreResult,
            AgentCase,
            AgentReview,
        ):
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self._seed_active_rules()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _seed_active_rules(self) -> None:
        self.session.add(FraudRuleSet(id=1, version=1, status="ACTIVE"))
        self.session.flush()
        for index, fraud_type in enumerate(FRAUD_TYPES, start=1):
            rule_id = index * 10
            self.session.add(
                FraudRule(
                    id=rule_id,
                    rule_set_id=1,
                    type_code=fraud_type,
                    display_name=fraud_type,
                    sort_order=index,
                )
            )
        self.session.flush()
        for index, _fraud_type in enumerate(FRAUD_TYPES, start=1):
            rule_id = index * 10
            self.session.add_all(
                [
                    FraudRuleComponent(
                        id=rule_id + component_index,
                        rule_id=rule_id,
                        component_key=f"evidence_{component_index}",
                        name=f"근거 {component_index}",
                        condition_expression={"field": f"feature_{component_index}"},
                        weight=0.20,
                        sort_order=component_index,
                    )
                    for component_index in range(1, 4)
                ]
            )
        self.session.commit()

    def test_creates_six_reviewed_cases_for_each_fraud_type(self) -> None:
        result = seed_agent_similar_cases(self.session)

        self.assertEqual(result.created_count, 24)
        self.assertEqual(result.skipped_count, 0)
        reviews = list(self.session.exec(select(AgentReview)).all())
        self.assertEqual(len(reviews), 24)
        for fraud_type in FRAUD_TYPES:
            score_results = list(
                self.session.exec(
                    select(FraudTypeScoreResult).where(
                        FraudTypeScoreResult.primary_fraud_type == fraud_type
                    )
                ).all()
            )
            case_ids = {
                case.case_id
                for case in self.session.exec(select(AgentCase)).all()
                if case.fraud_type_score_result_id
                in {score.id for score in score_results}
            }
            type_reviews = [
                review for review in reviews if review.case_id in case_ids
            ]
            self.assertEqual(len(type_reviews), 6)
            self.assertEqual(
                sum(
                    review.confirmed_fraud_type == fraud_type
                    for review in type_reviews
                ),
                6,
            )
            self.assertTrue(
                all(review.decision == "CONFIRMED_FRAUD" for review in type_reviews)
            )
        self.assertTrue(
            all(
                case.execution_status == "COMPLETED"
                for case in self.session.exec(select(AgentCase)).all()
            )
        )

    def test_second_execution_skips_existing_cases(self) -> None:
        seed_agent_similar_cases(self.session)
        existing_case = self.session.get(AgentCase, "DEMO-CASE-01-01")
        existing_review = self.session.get(AgentReview, "DEMO-CASE-01-01")
        existing_case.risk_score = 99
        existing_case.risk_grade = "VERY_HIGH"
        existing_review.decision = "FALSE_POSITIVE"
        existing_review.confirmed_fraud_type = None
        self.session.commit()

        result = seed_agent_similar_cases(self.session)

        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.skipped_count, 24)
        self.assertEqual(len(self.session.exec(select(AgentCase)).all()), 24)
        self.assertEqual(existing_case.risk_score, 58)
        self.assertEqual(existing_case.risk_grade, "MEDIUM")
        self.assertEqual(existing_review.decision, "CONFIRMED_FRAUD")
        self.assertEqual(existing_review.confirmed_fraud_type, VOICE_PHISHING)

    def test_seeded_cases_are_used_by_dashboard_top_three_search(self) -> None:
        seed_agent_similar_cases(self.session)
        # MEDIUM 위험 사건도 동일 등급과 유사한 Rule 근거를 가진 완료 사건을
        # 찾는지 확인한다. 기존 Seed는 모두 VERY_HIGH라 이 경로가 비어 있었다.
        current_case = self.session.get(AgentCase, "DEMO-CASE-03-01")
        self.assertIsNotNone(current_case)
        score_result = self.session.get(
            FraudTypeScoreResult,
            current_case.fraud_type_score_result_id,
        )
        self.assertIsNotNone(score_result)
        evidence = [
            RuleEvidenceDTO(
                fraud_type=fraud_type,
                evidence_code=evidence_code,
                observed_value=True,
                contribution=0.20,
            )
            for fraud_type, evidence_codes in score_result.matched_components.items()
            for evidence_code in evidence_codes
        ]
        rule_result = FraudTypeScoreResultDTO(
            fraud_type_score_result_id=score_result.id,
            rule_filter_status=RuleFilterStatus.APPLIED,
            primary_fraud_type=score_result.primary_fraud_type,
            type_scores=score_result.type_scores,
            matched_components=evidence,
        )
        service = DashboardSimilarCaseService(
            DatabaseSimilarCaseTools(
                AgentInvestigationRepository(self.session)
            )
        )

        results = service.find_top_three(
            current_case_id=current_case.case_id,
            rule_result=rule_result,
            risk_score=current_case.risk_score,
            risk_grade=current_case.risk_grade,
        )

        self.assertEqual(len(results), 3)
        self.assertNotIn(
            current_case.case_id,
            [result.similar_case_id for result in results],
        )
        self.assertEqual(
            [result.similarity_rank for result in results],
            [1, 2, 3],
        )
        candidate_reviews = [
            self.session.get(AgentReview, result.similar_case_id)
            for result in results
        ]
        self.assertTrue(
            all(review.decision == "CONFIRMED_FRAUD" for review in candidate_reviews)
        )


if __name__ == "__main__":
    unittest.main()
