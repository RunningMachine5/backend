import unittest
from datetime import UTC, datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.data.model.agent import AgentCase
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudTypeScoreResult,
)
from app.data.model.transaction import Transaction
from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InformationStatus,
    InvestigationStatus,
)
from app.domain.enums import RiskGrade
from app.dto.agent import (
    AgentInputDTO,
    ChecklistItemDTO,
    InvestigationResultDTO,
    RecommendedActionDTO,
    ResponsePlanDTO,
    SimilarCaseResultDTO,
)
from app.repositories.agent_case import AgentCaseRepository
from app.services.agent.case_service import (
    AgentCaseNotFoundError,
    AgentCaseService,
    InvalidAgentCaseTransitionError,
)


class AgentCaseServiceTest(unittest.TestCase):
    """실제 SQLite 세션에서 사건 생성과 상태 전이를 검증한다."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Transaction.__table__.create(self.engine)
        FraudRuleSet.__table__.create(self.engine)
        FraudRule.__table__.create(self.engine)
        FraudRuleComponent.__table__.create(self.engine)
        FraudTypeScoreResult.__table__.create(self.engine)
        AgentCase.__table__.create(self.engine)

        self.session = Session(self.engine)
        self.repository = AgentCaseRepository(self.session)
        self.finished_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        self.service = AgentCaseService(
            self.repository,
            case_id_factory=lambda: "CASE-20260811-TEST0001",
            now_factory=lambda: self.finished_at,
        )
        self._seed_detection_result()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _seed_detection_result(self) -> None:
        self.session.add(
            Transaction(
                id="TX-001",
                customer_id="CUSTOMER-001",
                source_account_number="ACCOUNT-001",
                recipient_account_number=None,
                transaction_datetime=datetime(2026, 8, 11, 10, 0),
                transaction_amount=9_500_000,
                channel="mobile",
                type_general_automatic="general",
                access_medium="a",
                error_code="a",
                num_connection_failure=0,
                another_person_account=True,
                initial_balance=10_000_000,
                balance=500_000,
                remaining_amount_daily_limit_exceeded=0,
                operating_system="Android",
                location="서울특별시",
                rooting_jailbreak_indicator=False,
                mobile_roaming_indicator=False,
                vpn_indicator=False,
                flag_terminal_malicious_behavior_1=False,
                flag_terminal_malicious_behavior_2=True,
                flag_terminal_malicious_behavior_3=False,
                flag_terminal_malicious_behavior_5=False,
                flag_terminal_malicious_behavior_6=False,
            )
        )
        self.session.add(FraudRuleSet(id=1, version=1, status="ACTIVE"))
        self.session.add_all(
            [
                FraudRule(
                    id=11,
                    rule_set_id=1,
                    type_code="ACCOUNT_TAKEOVER",
                    display_name="계정탈취",
                    sort_order=1,
                ),
                FraudRule(
                    id=12,
                    rule_set_id=1,
                    type_code="MESSENGER_PHISHING",
                    display_name="메신저피싱",
                    sort_order=2,
                ),
            ]
        )
        self.session.add(
            FraudRuleComponent(
                id=21,
                rule_id=11,
                component_key="remote_control",
                name="원격제어",
                condition_expression={"field": "remote_control"},
                weight=0.20,
            )
        )
        self.session.add(
            FraudTypeScoreResult(
                id=7,
                transaction_id="TX-001",
                rule_set_id=1,
                rule_filter_status="APPLIED",
                primary_fraud_type="ACCOUNT_TAKEOVER",
                type_scores={
                    "ACCOUNT_TAKEOVER": 0.62,
                    "MESSENGER_PHISHING": 0.57,
                    "VOICE_PHISHING": 0.20,
                    "FRAUD_USED_ACCOUNT": 0.10,
                },
                matched_components={
                    "ACCOUNT_TAKEOVER": ["remote_control"],
                },
            )
        )
        self.session.commit()

    @staticmethod
    def _input() -> AgentInputDTO:
        return AgentInputDTO(
            transaction_id="TX-001",
            fraud_type_score_result_id=7,
            risk_score=92,
            risk_grade=RiskGrade.VERY_HIGH,
        )

    def test_start_case_creates_processing_case_and_confidence_result(self) -> None:
        result = self.service.start_case(self._input())

        self.assertTrue(result.created)
        self.assertEqual(result.response.case_id, "CASE-20260811-TEST0001")
        self.assertEqual(
            result.response.execution_status,
            AgentExecutionStatus.PROCESSING,
        )
        self.assertEqual(
            result.type_confidence.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )
        self.assertEqual(result.type_confidence.score_margin, 0.05)

    def test_duplicate_start_returns_existing_case(self) -> None:
        first = self.service.start_case(self._input())
        second = self.service.start_case(self._input())

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.response.case_id, second.response.case_id)
        cases = self.session.exec(select(AgentCase)).all()
        self.assertEqual(len(cases), 1)

    def test_complete_case_persists_nested_results(self) -> None:
        started = self.service.start_case(self._input())
        investigation = InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=0.05,
            investigation_status=InvestigationStatus.COMPLETED,
            recommended_fraud_type="ACCOUNT_TAKEOVER",
            recommendation_reason="공통 Rule 근거가 충분하다.",
            best_similarity_score=0.91,
            common_evidence_codes=["remote_control"],
            confirmed_case_count=2,
        )
        similar_cases = [
            SimilarCaseResultDTO(
                similar_case_id="CASE-HISTORY-001",
                similarity_rank=1,
                similarity_score=0.91,
                similarity_reason="원격제어 근거가 일치한다.",
            )
        ]
        response_plan = ResponsePlanDTO(
            applied_fraud_type="ACCOUNT_TAKEOVER",
            information_status=InformationStatus.SUFFICIENT,
            summary="계정탈취 의심 사건의 대응 계획이다.",
            recommended_actions=[
                RecommendedActionDTO(
                    priority=1,
                    action_code="VERIFY_CUSTOMER_TRANSACTION",
                    action="고객 본인 거래 여부를 확인한다.",
                    reason="원격제어 정황이 확인되었다.",
                    required=True,
                    procedure_steps=["등록된 연락처로 고객에게 연락한다."],
                    cautions=["민감정보를 먼저 요구하지 않는다."],
                )
            ],
            checklist=[
                ChecklistItemDTO(
                    item_code="CUSTOMER_CONFIRMED",
                    label="고객 본인 거래 여부 확인",
                    required=True,
                )
            ],
        )

        completed = self.service.complete_case(
            started.response.case_id,
            investigation_result=investigation,
            similar_case_results=similar_cases,
            response_result=response_plan,
            generation_metadata={"duration_ms": 850},
        )

        self.assertEqual(completed.execution_status, AgentExecutionStatus.COMPLETED)
        # SQLite는 timezone 정보를 제거하므로 저장된 시각 값 자체를 비교한다.
        self.assertEqual(
            completed.completed_at.replace(tzinfo=UTC),
            self.finished_at,
        )
        self.assertEqual(completed.investigation_result.score_margin, 0.05)
        self.assertEqual(completed.similar_case_results[0].similarity_rank, 1)
        self.assertEqual(
            completed.response_result.recommended_actions[0].action_code,
            "VERIFY_CUSTOMER_TRANSACTION",
        )
        self.assertEqual(completed.generation_metadata["duration_ms"], 850)

    def test_fail_case_saves_reason_and_terminal_time(self) -> None:
        started = self.service.start_case(self._input())

        failed = self.service.fail_case(
            started.response.case_id,
            failure_reason="유사 사건 조회 시간이 초과되었다.",
            generation_metadata={"failed_step": "similar_case_search"},
        )

        self.assertEqual(failed.execution_status, AgentExecutionStatus.FAILED)
        self.assertEqual(failed.failure_reason, "유사 사건 조회 시간이 초과되었다.")
        self.assertEqual(
            failed.completed_at.replace(tzinfo=UTC),
            self.finished_at,
        )
        self.assertEqual(
            failed.generation_metadata["failed_step"],
            "similar_case_search",
        )

    def test_failed_case_cannot_be_completed(self) -> None:
        started = self.service.start_case(self._input())
        self.service.fail_case(
            started.response.case_id,
            failure_reason="테스트 실패",
        )

        with self.assertRaises(InvalidAgentCaseTransitionError):
            self.service.complete_case(
                started.response.case_id,
                investigation_result=None,
                similar_case_results=[],
                response_result=ResponsePlanDTO(
                    applied_fraud_type="ACCOUNT_TAKEOVER",
                    information_status=InformationStatus.PARTIAL,
                    summary="응답",
                    recommended_actions=[],
                    checklist=[],
                ),
            )

    def test_start_case_requires_saved_transaction(self) -> None:
        missing_input = AgentInputDTO(
            transaction_id="TX-MISSING",
            fraud_type_score_result_id=7,
            risk_score=80,
            risk_grade=RiskGrade.HIGH,
        )

        with self.assertRaisesRegex(AgentCaseNotFoundError, "거래"):
            self.service.start_case(missing_input)


if __name__ == "__main__":
    unittest.main()
