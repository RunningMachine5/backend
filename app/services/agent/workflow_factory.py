"""Agent API와 백그라운드 작업이 사용하는 Workflow를 조립한다."""

from sqlmodel import Session

from app.repositories.agent_case import AgentCaseRepository
from app.repositories.agent_email import AgentEmailRepository
from app.repositories.agent_guide import AgentGuideRepository
from app.repositories.agent_investigation import AgentInvestigationRepository
from app.services.agent.case_service import AgentCaseService
from app.services.agent.dashboard_similar_cases import DashboardSimilarCaseService
from app.services.agent.email_sender import FraudAlertEmailService
from app.services.agent.guide_embedder import OpenAIGuideEmbedder
from app.services.agent.guide_search import GuideSearchService
from app.services.agent.response_plan_generator import RagResponsePlanGenerator
from app.services.agent.response_policy import get_default_policy_repository
from app.services.agent.similar_case_investigator import (
    DatabaseSimilarCaseTools,
    LimitedSimilarCaseInvestigator,
    OpenAIInvestigationActionSelector,
)
from app.services.agent.workflow import AgentWorkflow


def create_agent_workflow(session: Session) -> AgentWorkflow:
    """하나의 DB Session에 연결된 Agent Workflow를 생성한다."""

    similar_case_tools = DatabaseSimilarCaseTools(
        AgentInvestigationRepository(session)
    )
    return AgentWorkflow(
        case_service=AgentCaseService(AgentCaseRepository(session)),
        policy_repository=get_default_policy_repository(),
        guide_search_service=GuideSearchService(
            AgentGuideRepository(session),
            OpenAIGuideEmbedder(),
        ),
        investigator=LimitedSimilarCaseInvestigator(
            similar_case_tools,
            OpenAIInvestigationActionSelector(),
        ),
        response_plan_generator=RagResponsePlanGenerator(),
        email_notifier=FraudAlertEmailService.from_env(
            AgentEmailRepository(session)
        ),
        dashboard_similar_case_finder=DashboardSimilarCaseService(
            similar_case_tools
        ),
    )


__all__ = ["create_agent_workflow"]
