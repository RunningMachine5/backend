"""Agent 사건의 생성, 중복 방지, 실행 상태 전이를 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.data.model.agent import AgentCase
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.domain.agent_status import AgentExecutionStatus
from app.dto.agent import (
    AgentInputDTO,
    AgentResponseDTO,
    InvestigationResultDTO,
    ResponsePlanDTO,
    SimilarCaseResultDTO,
    dto_to_dict,
)
from app.repositories.agent_case import AgentCaseRepository
from app.services.agent.case_mapper import (
    build_agent_input_dto,
    to_agent_response_dto,
)
from app.services.agent.type_confidence import (
    TypeConfidenceResult,
    calculate_type_confidence,
)


class AgentCaseNotFoundError(LookupError):
    """Agent 실행에 필요한 거래, Rule 결과 또는 사건이 없는 경우이다."""


class InvalidAgentCaseTransitionError(RuntimeError):
    """이미 종료된 사건을 다른 종료 상태로 바꾸려는 경우이다."""


@dataclass(frozen=True, slots=True)
class AgentCaseStartResult:
    """사건 생성 결과와 다음 Graph 분기에 필요한 유형 확실성 결과이다."""

    response: AgentResponseDTO
    type_confidence: TypeConfidenceResult
    created: bool


class AgentCaseService:
    """DB에 저장된 탐지 결과를 Agent 실행 가능한 사건으로 전환한다."""

    def __init__(
        self,
        repository: AgentCaseRepository,
        *,
        case_id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.case_id_factory = case_id_factory or _generate_case_id
        self.now_factory = now_factory or (lambda: datetime.now(UTC))

    def start_case(self, agent_input: AgentInputDTO) -> AgentCaseStartResult:
        """입력을 확인하고 중복되지 않은 PROCESSING 사건을 생성한다."""

        transaction = self.repository.get_transaction(agent_input.transaction_id)
        if transaction is None:
            raise AgentCaseNotFoundError(
                f"거래를 찾을 수 없다: {agent_input.transaction_id}"
            )

        score_result = self.repository.get_score_result(
            agent_input.fraud_type_score_result_id
        )
        if score_result is None:
            raise AgentCaseNotFoundError(
                "Rule 유형 점수 결과를 찾을 수 없다: "
                f"{agent_input.fraud_type_score_result_id}"
            )

        # Mapper의 동일 거래·위험점수 검증을 실행 경계에서도 재사용한다.
        build_agent_input_dto(
            transaction,
            score_result,
            risk_score=agent_input.risk_score,
            risk_grade=agent_input.risk_grade,
        )

        existing = self._find_existing(agent_input)
        if existing is not None:
            return self._start_result(existing, score_result, created=False)

        agent_case = self.repository.add_processing_case(
            case_id=self.case_id_factory(),
            transaction_id=agent_input.transaction_id,
            fraud_type_score_result_id=agent_input.fraud_type_score_result_id,
            risk_score=agent_input.risk_score,
            risk_grade=agent_input.risk_grade.value,
        )
        try:
            self.repository.commit()
            self.repository.refresh(agent_case)
        except IntegrityError:
            # 동시에 같은 요청이 들어오면 UNIQUE 제약에서 진 요청은 기존 사건을 반환한다.
            self.repository.rollback()
            existing = self._find_existing(agent_input)
            if existing is None:
                raise
            return self._start_result(existing, score_result, created=False)
        except Exception:
            self.repository.rollback()
            raise

        return self._start_result(agent_case, score_result, created=True)

    def get_case(self, case_id: str) -> AgentResponseDTO:
        """저장된 사건을 대시보드 응답 DTO로 조회한다."""

        agent_case = self._require_case(case_id)
        return self._to_response(agent_case)

    def get_case_by_transaction(self, transaction_id: str) -> AgentResponseDTO:
        """거래 식별자에 연결된 Agent 사건을 조회한다."""

        agent_case = self.repository.find_case_by_transaction(transaction_id)
        if agent_case is None:
            raise AgentCaseNotFoundError(
                f"거래에 연결된 Agent 사건을 찾을 수 없다: {transaction_id}"
            )
        return self._to_response(agent_case)

    def complete_case(
        self,
        case_id: str,
        *,
        investigation_result: InvestigationResultDTO | None,
        similar_case_results: list[SimilarCaseResultDTO],
        response_result: ResponsePlanDTO,
        generation_metadata: dict[str, Any] | None = None,
        best_similar_case_id: str | None = None,
    ) -> AgentResponseDTO:
        """조사 및 대응 계획 결과를 저장하고 사건 실행을 완료한다."""

        agent_case = self._require_case(case_id)
        if agent_case.execution_status == AgentExecutionStatus.COMPLETED.value:
            return self._to_response(agent_case)
        self._require_processing(agent_case)

        self.repository.mark_completed(
            agent_case,
            investigation_result=(
                dto_to_dict(investigation_result)
                if investigation_result is not None
                else None
            ),
            best_similar_case_id=best_similar_case_id,
            similar_case_results=dto_to_dict(similar_case_results),
            response_result=dto_to_dict(response_result),
            generation_metadata={
                **(agent_case.generation_metadata or {}),
                **(generation_metadata or {}),
            },
            completed_at=self.now_factory(),
        )
        self._commit_and_refresh(agent_case)
        return self._to_response(agent_case)

    def fail_case(
        self,
        case_id: str,
        *,
        failure_reason: str,
        generation_metadata: dict[str, Any] | None = None,
    ) -> AgentResponseDTO:
        """실행 중 발생한 오류를 저장하고 사건을 실패 상태로 종료한다."""

        agent_case = self._require_case(case_id)
        if agent_case.execution_status == AgentExecutionStatus.FAILED.value:
            return self._to_response(agent_case)
        self._require_processing(agent_case)

        reason = failure_reason.strip()
        if not reason:
            raise ValueError("failure_reason은 비어 있을 수 없다.")

        self.repository.mark_failed(
            agent_case,
            failure_reason=reason,
            generation_metadata={
                **(agent_case.generation_metadata or {}),
                **(generation_metadata or {}),
            },
            completed_at=self.now_factory(),
        )
        self._commit_and_refresh(agent_case)
        return self._to_response(agent_case)

    def _find_existing(self, agent_input: AgentInputDTO) -> AgentCase | None:
        by_transaction = self.repository.find_case_by_transaction(
            agent_input.transaction_id
        )
        if by_transaction is not None:
            if (
                by_transaction.fraud_type_score_result_id
                != agent_input.fraud_type_score_result_id
            ):
                raise ValueError("거래에 다른 Rule 결과를 사용한 Agent 사건이 존재한다.")
            return by_transaction

        by_score = self.repository.find_case_by_score_result(
            agent_input.fraud_type_score_result_id
        )
        if (
            by_score is not None
            and by_score.transaction_id != agent_input.transaction_id
        ):
            raise ValueError("Rule 결과가 다른 거래의 Agent 사건에 연결되어 있다.")
        return by_score

    def _start_result(
        self,
        agent_case: AgentCase,
        score_result: FraudTypeScoreResult,
        *,
        created: bool,
    ) -> AgentCaseStartResult:
        return AgentCaseStartResult(
            response=self._to_response(agent_case, score_result=score_result),
            type_confidence=calculate_type_confidence(score_result.type_scores),
            created=created,
        )

    def _to_response(
        self,
        agent_case: AgentCase,
        *,
        score_result: FraudTypeScoreResult | None = None,
    ) -> AgentResponseDTO:
        resolved_score = score_result or self.repository.get_score_result(
            agent_case.fraud_type_score_result_id
        )
        if resolved_score is None:
            raise AgentCaseNotFoundError(
                "Agent 사건의 Rule 유형 점수 결과를 찾을 수 없다."
            )
        rules = self.repository.list_rules(resolved_score.rule_set_id)
        rule_ids = [rule.id for rule in rules if rule.id is not None]
        components = self.repository.list_components(rule_ids)
        return to_agent_response_dto(
            agent_case,
            resolved_score,
            rules=rules,
            components=components,
        )

    def _require_case(self, case_id: str) -> AgentCase:
        agent_case = self.repository.get_case(case_id)
        if agent_case is None:
            raise AgentCaseNotFoundError(f"Agent 사건을 찾을 수 없다: {case_id}")
        return agent_case

    @staticmethod
    def _require_processing(agent_case: AgentCase) -> None:
        if agent_case.execution_status != AgentExecutionStatus.PROCESSING.value:
            raise InvalidAgentCaseTransitionError(
                f"{agent_case.execution_status} 사건의 종료 상태를 변경할 수 없다."
            )

    def _commit_and_refresh(self, agent_case: AgentCase) -> None:
        """상태 저장에 실패하면 세션을 다음 요청에서 재사용할 수 있도록 복구한다."""

        try:
            self.repository.commit()
            self.repository.refresh(agent_case)
        except Exception:
            self.repository.rollback()
            raise


def _generate_case_id() -> str:
    """사람이 식별하기 쉬운 날짜와 충돌 방지용 난수를 조합한다."""

    date_part = datetime.now(UTC).strftime("%Y%m%d")
    return f"CASE-{date_part}-{uuid4().hex[:8].upper()}"


__all__ = [
    "AgentCaseNotFoundError",
    "AgentCaseService",
    "AgentCaseStartResult",
    "InvalidAgentCaseTransitionError",
]
