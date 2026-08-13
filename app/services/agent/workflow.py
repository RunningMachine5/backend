"""탐지 결과를 이메일 명령과 대응 계획으로 연결하는 LangGraph 실행 흐름이다."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.domain.agent_status import ClassificationStatus, InvestigationStatus
from app.domain.response_policy import PolicyRepository, ResponsePolicy
from app.dto.agent import (
    AgentInputDTO,
    AgentResponseDTO,
    FraudAlertEmailCommand,
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    ResponsePlanDTO,
)
from app.dto.agent_guide import GuideSearchRequestDTO, RetrievedGuideChunkDTO
from app.services.agent.case_service import AgentCaseService
from app.services.agent.email_command_builder import build_fraud_alert_email_command
from app.services.agent.guide_search import GuideSearchService
from app.services.agent.response_plan_generator import PolicyResponsePlanGenerator
from app.services.agent.type_confidence import TypeConfidenceResult


class AgentGraphState(TypedDict, total=False):
    """Graph Node가 공유하는 DTO와 단계별 계산 결과이다."""

    agent_input: AgentInputDTO
    case_id: str
    case_created: bool
    rule_result: FraudTypeScoreResultDTO
    type_confidence: TypeConfidenceResult
    investigation_result: InvestigationResultDTO | None
    applied_fraud_type: str
    email_command: FraudAlertEmailCommand
    response_policy: ResponsePolicy
    retrieved_guides: list[RetrievedGuideChunkDTO]
    response_plan: ResponsePlanDTO
    final_response: AgentResponseDTO
    failure_reason: str
    workflow_started_at: float
    investigation_metrics: dict[str, object]


class AmbiguousTypeInvestigator(Protocol):
    """애매한 유형 사건을 조사하는 후속 ReAct Agent의 연결 계약이다."""

    def investigate(
        self,
        *,
        case_id: str,
        rule_result: FraudTypeScoreResultDTO,
        confidence: TypeConfidenceResult,
        risk_score: int,
        risk_grade: str,
        metrics: dict[str, object] | None = None,
    ) -> InvestigationResultDTO: ...


class ResponsePlanGenerator(Protocol):
    """정책과 검색 문맥으로 대응 계획을 만드는 생성기의 연결 계약이다."""

    def generate(
        self,
        *,
        fraud_type: str,
        policy: ResponsePolicy,
        guides: list[RetrievedGuideChunkDTO],
    ) -> ResponsePlanDTO: ...


class RuleFirstFallbackInvestigator:
    """ReAct 구현 전에는 Rule 1순위를 유지하고 근거 부족 상태를 남긴다."""

    def investigate(
        self,
        *,
        case_id: str,
        rule_result: FraudTypeScoreResultDTO,
        confidence: TypeConfidenceResult,
        risk_score: int,
        risk_grade: str,
        metrics: dict[str, object] | None = None,
    ) -> InvestigationResultDTO:
        del case_id, rule_result, risk_score, risk_grade
        result = InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=confidence.score_margin,
            investigation_status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
            recommended_fraud_type=None,
            recommendation_reason=(
                "유사 완료 사건 조사 기능이 연결되지 않아 Rule 1순위 유형을 유지한다."
            ),
            best_similarity_score=None,
            common_evidence_codes=[],
            confirmed_case_count=0,
        )
        if metrics is not None:
            metrics.update(
                {
                    "investigation_latency_ms": 0,
                    "react_llm_call_count": 0,
                    "api_attempt_count": 0,
                    "retry_count": 0,
                    "tool_call_count": 0,
                    "fallback_used": True,
                    "fallback_reason": result.recommendation_reason,
                }
            )
        return result


class AgentWorkflow:
    """기존 Agent 서비스들을 LangGraph Node로 조합하고 최종 DTO를 반환한다."""

    def __init__(
        self,
        *,
        case_service: AgentCaseService,
        policy_repository: PolicyRepository,
        guide_search_service: GuideSearchService,
        investigator: AmbiguousTypeInvestigator | None = None,
        response_plan_generator: ResponsePlanGenerator | None = None,
    ) -> None:
        self.case_service = case_service
        self.policy_repository = policy_repository
        self.guide_search_service = guide_search_service
        self.investigator = investigator or RuleFirstFallbackInvestigator()
        self.response_plan_generator = (
            response_plan_generator or PolicyResponsePlanGenerator()
        )
        self.graph = self._build_graph()

    def run(self, agent_input: AgentInputDTO) -> AgentResponseDTO:
        """Agent 입력 한 건을 실행하고 DB에 저장된 최종 사건 DTO를 반환한다."""

        result = self.run_state(agent_input)
        return result["final_response"]

    def run_state(self, agent_input: AgentInputDTO) -> AgentGraphState:
        """이메일 명령을 포함한 전체 실행 State가 필요한 연결 계층에서 사용한다."""

        return self.graph.invoke(
            {
                "agent_input": agent_input,
                "workflow_started_at": time.perf_counter(),
                "investigation_metrics": {},
            }
        )

    def _build_graph(self):
        graph = StateGraph(AgentGraphState)
        graph.add_node("start_case", self._safe(self._start_case))
        graph.add_node("use_rule_type", self._safe(self._use_rule_type))
        graph.add_node("investigate_type", self._safe(self._investigate_type))
        graph.add_node("build_email_command", self._safe(self._build_email_command))
        graph.add_node("load_policy", self._safe(self._load_policy))
        graph.add_node("search_guides", self._safe(self._search_guides))
        graph.add_node("generate_plan", self._safe(self._generate_plan))
        graph.add_node("complete_case", self._safe(self._complete_case))
        graph.add_node("fail_case", self._fail_case)

        graph.add_edge(START, "start_case")
        graph.add_conditional_edges(
            "start_case",
            self._route_after_start,
            {
                "existing": END,
                "confident": "use_rule_type",
                "ambiguous": "investigate_type",
                "failed": "fail_case",
            },
        )
        self._add_failure_route(graph, "use_rule_type", "build_email_command")
        self._add_failure_route(graph, "investigate_type", "build_email_command")
        self._add_failure_route(graph, "build_email_command", "load_policy")
        self._add_failure_route(graph, "load_policy", "search_guides")
        self._add_failure_route(graph, "search_guides", "generate_plan")
        self._add_failure_route(graph, "generate_plan", "complete_case")
        graph.add_conditional_edges(
            "complete_case",
            lambda state: "failed" if state.get("failure_reason") else "completed",
            {"failed": "fail_case", "completed": END},
        )
        graph.add_edge("fail_case", END)
        return graph.compile()

    @staticmethod
    def _add_failure_route(graph: StateGraph, node: str, next_node: str) -> None:
        graph.add_conditional_edges(
            node,
            lambda state: "failed" if state.get("failure_reason") else "continue",
            {"failed": "fail_case", "continue": next_node},
        )

    @staticmethod
    def _safe(
        node: Callable[[AgentGraphState], dict[str, object]],
    ) -> Callable[[AgentGraphState], dict[str, object]]:
        """Node 오류를 실패 경로가 처리할 수 있는 State 값으로 변환한다."""

        def wrapped(state: AgentGraphState) -> dict[str, object]:
            try:
                return node(state)
            except Exception as error:
                return {"failure_reason": str(error)}

        return wrapped

    def _start_case(self, state: AgentGraphState) -> dict[str, object]:
        started = self.case_service.start_case(state["agent_input"])
        return {
            "case_id": started.response.case_id,
            "case_created": started.created,
            "rule_result": started.response.rule_result,
            # 사건 생성 서비스가 계산한 결과를 재사용하여 중복 계산하지 않는다.
            "type_confidence": started.type_confidence,
            "final_response": started.response,
        }

    @staticmethod
    def _use_rule_type(state: AgentGraphState) -> dict[str, object]:
        confidence = state["type_confidence"]
        return {
            "applied_fraud_type": confidence.top_type_code,
            "investigation_result": InvestigationResultDTO(
                classification_status=ClassificationStatus.CONFIDENT,
                score_margin=confidence.score_margin,
                investigation_status=InvestigationStatus.NOT_REQUIRED,
                recommended_fraud_type=None,
                recommendation_reason=None,
                best_similarity_score=None,
                common_evidence_codes=[],
                confirmed_case_count=0,
            ),
        }

    def _investigate_type(self, state: AgentGraphState) -> dict[str, object]:
        agent_input = state["agent_input"]
        confidence = state["type_confidence"]
        metrics: dict[str, object] = {}
        result = self.investigator.investigate(
            case_id=state["case_id"],
            rule_result=state["rule_result"],
            confidence=confidence,
            risk_score=agent_input.risk_score,
            risk_grade=agent_input.risk_grade.value,
            metrics=metrics,
        )
        candidate_types = {confidence.top_type_code, confidence.second_type_code}
        applied_type = (
            result.recommended_fraud_type
            if result.investigation_status is InvestigationStatus.COMPLETED
            and result.recommended_fraud_type in candidate_types
            else confidence.top_type_code
        )
        return {
            "investigation_result": result,
            "applied_fraud_type": applied_type,
            "investigation_metrics": metrics,
        }

    @staticmethod
    def _build_email_command(state: AgentGraphState) -> dict[str, object]:
        return {
            "email_command": build_fraud_alert_email_command(
                transaction_id=state["agent_input"].transaction_id,
                type_confidence=state["type_confidence"],
                investigation_result=state["investigation_result"],
            )
        }

    def _load_policy(self, state: AgentGraphState) -> dict[str, object]:
        policy = self.policy_repository.get_response_policy(
            fraud_type=state["applied_fraud_type"],
            risk_grade=state["agent_input"].risk_grade.value,
        )
        return {"response_policy": policy}

    def _search_guides(self, state: AgentGraphState) -> dict[str, object]:
        policy = state["response_policy"]
        query = " ".join(
            [state["applied_fraud_type"], policy.risk_grade]
            + [action.action for action in policy.actions]
        )
        guides = self.guide_search_service.search(
            GuideSearchRequestDTO(
                query=query,
                fraud_type=state["applied_fraud_type"],
                audience="MONITORING",
                risk_grade=policy.risk_grade,
                action_codes=tuple(action.action_code for action in policy.actions),
                top_k=5,
            )
        )
        return {"retrieved_guides": guides}

    def _generate_plan(self, state: AgentGraphState) -> dict[str, object]:
        return {
            "response_plan": self.response_plan_generator.generate(
                fraud_type=state["applied_fraud_type"],
                policy=state["response_policy"],
                guides=state["retrieved_guides"],
            )
        }

    def _complete_case(self, state: AgentGraphState) -> dict[str, object]:
        metrics = {
            "total_latency_ms": round(
                (time.perf_counter() - state["workflow_started_at"]) * 1000
            ),
            "investigation_latency_ms": 0,
            "react_llm_call_count": 0,
            "api_attempt_count": 0,
            "retry_count": 0,
            "tool_call_count": 0,
            "fallback_used": False,
            "fallback_reason": None,
            **state.get("investigation_metrics", {}),
        }
        response = self.case_service.complete_case(
            state["case_id"],
            investigation_result=state["investigation_result"],
            similar_case_results=[],
            response_result=state["response_plan"],
            generation_metadata={
                "workflow_version": "1.0",
                "classification_status": state[
                    "type_confidence"
                ].classification_status.value,
                "retrieved_guide_count": len(state["retrieved_guides"]),
                **metrics,
            },
        )
        return {"final_response": response}

    def _fail_case(self, state: AgentGraphState) -> dict[str, object]:
        case_id = state.get("case_id")
        if case_id is None:
            raise RuntimeError(state.get("failure_reason", "Agent 실행에 실패했다."))
        response = self.case_service.fail_case(
            case_id,
            failure_reason=state.get("failure_reason", "Agent 실행에 실패했다."),
            generation_metadata={
                "workflow_version": "1.0",
                "total_latency_ms": round(
                    (time.perf_counter() - state["workflow_started_at"]) * 1000
                ),
                "investigation_latency_ms": 0,
                "react_llm_call_count": 0,
                "api_attempt_count": 0,
                "retry_count": 0,
                "tool_call_count": 0,
                "fallback_used": True,
                "fallback_reason": state.get("failure_reason"),
                **state.get("investigation_metrics", {}),
            },
        )
        return {"final_response": response}

    @staticmethod
    def _route_after_start(state: AgentGraphState) -> str:
        if state.get("failure_reason"):
            return "failed"
        if not state.get("case_created"):
            return "existing"
        status = state["type_confidence"].classification_status
        return "ambiguous" if status is ClassificationStatus.AMBIGUOUS else "confident"


__all__ = [
    "AgentGraphState",
    "AgentWorkflow",
    "AmbiguousTypeInvestigator",
    "PolicyResponsePlanGenerator",
    "ResponsePlanGenerator",
    "RuleFirstFallbackInvestigator",
]
