"""Agent 워크플로우 실행과 사건 조회 API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import select

from app.core.db import SessionDep
from app.data.model.fraud_rule import FraudTypeScoreResult
from app.domain.agent_status import AgentExecutionStatus
from app.domain.enums import RiskGrade
from app.dto.agent import (
    AgentInputDTO,
    AgentResponseDTO,
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    ResponsePlanDTO,
    SimilarCaseResultDTO,
)
from app.repositories.agent_case import AgentCaseRepository
from app.repositories.agent_guide import AgentGuideRepository
from app.repositories.agent_investigation import AgentInvestigationRepository
from app.repositories.transaction import PredictionResultRepository
from app.services.agent.case_service import (
    AgentCaseNotFoundError,
    AgentCaseService,
)
from app.services.agent.guide_embedder import OpenAIGuideEmbedder
from app.services.agent.guide_search import GuideSearchService
from app.services.agent.response_policy import get_default_policy_repository
from app.services.agent.similar_case_investigator import (
    DatabaseSimilarCaseTools,
    LimitedSimilarCaseInvestigator,
    OpenAIInvestigationActionSelector,
)
from app.services.agent.workflow import AgentWorkflow
from app.services.analysis.risk_grader import RiskGrader


router = APIRouter(prefix="/api", tags=["agent-cases"])


class AgentCaseCreateRequest(BaseModel):
    """Agent 실행을 요청할 원본 거래 식별자."""

    model_config = ConfigDict(str_strip_whitespace=True)

    transaction_id: str


class AgentCaseResponse(BaseModel):
    """대시보드에 공개하는 Agent 사건 응답."""

    case_id: str
    transaction_id: str
    execution_status: AgentExecutionStatus
    failure_reason: str | None
    rule_result: FraudTypeScoreResultDTO
    risk_score: int
    risk_grade: RiskGrade
    investigation_result: InvestigationResultDTO | None
    best_similar_case_id: str | None
    similar_case_results: list[SimilarCaseResultDTO]
    response_result: ResponsePlanDTO | None


def get_agent_case_service(session: SessionDep) -> AgentCaseService:
    return AgentCaseService(AgentCaseRepository(session))


def get_agent_workflow(session: SessionDep) -> AgentWorkflow:
    case_service = get_agent_case_service(session)
    similar_case_tools = DatabaseSimilarCaseTools(
        AgentInvestigationRepository(session)
    )
    investigator = LimitedSimilarCaseInvestigator(
        similar_case_tools,
        OpenAIInvestigationActionSelector(),
    )
    guide_search = GuideSearchService(
        AgentGuideRepository(session),
        OpenAIGuideEmbedder(),
    )
    return AgentWorkflow(
        case_service=case_service,
        policy_repository=get_default_policy_repository(),
        guide_search_service=guide_search,
        investigator=investigator,
    )


AgentCaseServiceDep = Annotated[AgentCaseService, Depends(get_agent_case_service)]
AgentWorkflowDep = Annotated[AgentWorkflow, Depends(get_agent_workflow)]


def _public_response(response: AgentResponseDTO) -> AgentCaseResponse:
    return AgentCaseResponse(
        case_id=response.case_id,
        transaction_id=response.transaction_id,
        execution_status=response.execution_status,
        failure_reason=response.failure_reason,
        rule_result=response.rule_result,
        risk_score=response.risk_score,
        risk_grade=response.risk_grade,
        investigation_result=response.investigation_result,
        best_similar_case_id=response.best_similar_case_id,
        similar_case_results=response.similar_case_results,
        response_result=response.response_result,
    )


@router.post(
    "/agent-cases",
    response_model=AgentCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_case(
    payload: AgentCaseCreateRequest,
    session: SessionDep,
    workflow: AgentWorkflowDep,
) -> AgentCaseResponse:
    """저장된 탐지 결과로 Agent 대응 계획을 생성한다."""

    score_result = session.exec(
        select(FraudTypeScoreResult).where(
            FraudTypeScoreResult.transaction_id == payload.transaction_id
        )
    ).first()
    prediction = PredictionResultRepository(session).latest_for_transaction(
        payload.transaction_id
    )
    transaction = AgentCaseRepository(session).get_transaction(
        payload.transaction_id
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="거래를 찾을 수 없다.",
        )
    if score_result is None or score_result.id is None or prediction is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent 실행에 필요한 탐지 결과가 없다.",
        )

    risk = RiskGrader().assess(
        transaction.transaction_amount,
        prediction.fraud_probability,
    )
    response = workflow.run(
        AgentInputDTO(
            transaction_id=payload.transaction_id,
            fraud_type_score_result_id=score_result.id,
            risk_score=risk.risk_score,
            risk_grade=risk.risk_grade,
        )
    )
    return _public_response(response)


@router.get(
    "/agent-cases/{case_id}",
    response_model=AgentCaseResponse,
)
def get_agent_case(
    case_id: str,
    service: AgentCaseServiceDep,
) -> AgentCaseResponse:
    try:
        return _public_response(service.get_case(case_id))
    except AgentCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.get(
    "/transactions/{transaction_id}/agent-case",
    response_model=AgentCaseResponse,
)
def get_agent_case_by_transaction(
    transaction_id: str,
    service: AgentCaseServiceDep,
) -> AgentCaseResponse:
    try:
        return _public_response(service.get_case_by_transaction(transaction_id))
    except AgentCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
