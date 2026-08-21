"""Agent API와 백그라운드 작업이 사용하는 Workflow를 조립한다."""

from functools import lru_cache

from sqlmodel import Session

from app.repositories.agent_case import AgentCaseRepository
from app.repositories.agent_email import AgentEmailRepository
from app.repositories.agent_guide import AgentGuideRepository
from app.repositories.agent_investigation import AgentInvestigationRepository
from app.repositories.chat_session import ChatSessionRepository
from app.services.agent.case_service import AgentCaseService
from app.services.agent.dashboard_similar_cases import DashboardSimilarCaseService
from app.services.agent.email_sender import (
    FraudAlertEmailService,
    NoOpFraudAlertEmailService,
)
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
from app.services.chatbot.session_alert_notifier import ChatSessionAlertNotifier


@lru_cache(maxsize=1)
def get_response_plan_generator() -> RagResponsePlanGenerator:
    """백엔드 프로세스에서 대응 계획 생성기와 메모리 캐시를 공유한다."""

    return RagResponsePlanGenerator()


def create_agent_workflow(
    session: Session,
    *,
    send_email: bool = True,
) -> AgentWorkflow:
    """하나의 DB Session에 연결된 Agent Workflow를 생성한다."""

    similar_case_tools = DatabaseSimilarCaseTools(
        AgentInvestigationRepository(session)
    )
    email_notifier = (
        ChatSessionAlertNotifier(
            session=session,
            email_notifier=FraudAlertEmailService.from_env(
                AgentEmailRepository(session)
            ),
        )
        if send_email
        else NoOpFraudAlertEmailService()
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
        response_plan_generator=get_response_plan_generator(),
        email_notifier=email_notifier,
        dashboard_similar_case_finder=DashboardSimilarCaseService(
            similar_case_tools
        ),
        customer_response_provider=ChatSessionRepository(session),
    )


__all__ = ["create_agent_workflow", "get_response_plan_generator"]
