"""유형이 애매한 사건에 필요한 과거 완료 사건만 선택적으로 조사한다."""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from typing import Any, Protocol, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from app.domain.agent_status import ClassificationStatus, InvestigationStatus
from app.dto.agent import (
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    RuleEvidenceDTO,
)
from app.dto.agent_investigation import (
    InvestigationAction,
    InvestigationActionDTO,
    ResolvedCaseDetailDTO,
    SimilarResolvedCaseDTO,
)
from app.repositories.agent_investigation import AgentInvestigationRepository
from app.services.agent.case_similarity import (
    CaseSimilarityConfig,
    CaseSimilarityFeatures,
    rank_similar_cases,
)
from app.services.agent.type_confidence import TypeConfidenceResult


class SimilarCaseTools(Protocol):
    """조사 Agent가 선택하여 호출할 수 있는 읽기 전용 Tool 계약."""

    def search_similar_resolved_cases(
        self,
        *,
        current_case_id: str,
        candidate_fraud_types: tuple[str, str],
        type_scores: dict[str, float],
        evidence: list[RuleEvidenceDTO],
        risk_score: int,
        risk_grade: str,
        top_k: int,
    ) -> list[SimilarResolvedCaseDTO]: ...

    def get_resolved_case_detail(self, case_id: str) -> ResolvedCaseDetailDTO: ...


class InvestigationActionSelector(Protocol):
    """검색·상세조회 Observation을 보고 다음 행동을 선택하는 LLM 계약."""

    def select_action(
        self,
        *,
        candidate_fraud_types: tuple[str, str],
        similar_cases: list[SimilarResolvedCaseDTO],
        inspected_cases: list[ResolvedCaseDetailDTO],
        remaining_detail_calls: int,
    ) -> InvestigationActionDTO: ...


class InvestigationActionOutput(BaseModel):
    """LLM이 반환하는 다음 조사 행동의 구조화 출력."""

    model_config = ConfigDict(extra="forbid")

    action: InvestigationAction
    case_id: str | None
    recommended_fraud_type: str | None
    reason: str


class OpenAIInvestigationActionSelector:
    """OpenAI 구조화 출력으로 허용된 조사 행동만 선택한다."""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        model: str | None = None,
    ) -> None:
        self.structured_llm = structured_llm or ChatOpenAI(
            model=model
            or os.getenv(
                "AGENT_INVESTIGATION_MODEL",
                os.getenv("OPENAI_MODEL", "gpt-5"),
            ),
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
        ).with_structured_output(
            InvestigationActionOutput,
            method="json_schema",
            strict=True,
        )

    def select_action(
        self,
        *,
        candidate_fraud_types: tuple[str, str],
        similar_cases: list[SimilarResolvedCaseDTO],
        inspected_cases: list[ResolvedCaseDetailDTO],
        remaining_detail_calls: int,
    ) -> InvestigationActionDTO:
        payload = {
            "candidate_fraud_types": candidate_fraud_types,
            "similar_cases": [
                {
                    "case_id": case.case_id,
                    "similarity_score": case.similarity_score,
                    "common_evidence_codes": case.common_evidence_codes,
                }
                for case in similar_cases
            ],
            "inspected_cases": [
                {
                    "case_id": case.case_id,
                    "confirmed_fraud_type": case.confirmed_fraud_type,
                }
                for case in inspected_cases
            ],
            "remaining_detail_calls": remaining_detail_calls,
        }
        result = self.structured_llm.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "너는 금융 이상거래의 애매한 사기 유형을 조사하는 Agent다. "
                        "제공된 사건과 Rule 상위 후보만 사용한다. 아직 확인하지 않은 "
                        "사건 중 확인 가치가 있는 사건은 INSPECT_CASE로 선택한다. "
                        "근거가 충분하면 STOP_RECOMMEND, 부족하면 STOP_INSUFFICIENT를 "
                        "선택한다. 새로운 유형이나 사건 ID를 만들지 않는다."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ]
        )
        return InvestigationActionDTO(
            action=result.action,
            case_id=result.case_id,
            recommended_fraud_type=result.recommended_fraud_type,
            reason=result.reason,
        )


class InvestigationGraphState(TypedDict, total=False):
    """유사 사건 조사 서브그래프의 Reason·Action·Observation 상태."""

    case_id: str
    rule_result: FraudTypeScoreResultDTO
    confidence: TypeConfidenceResult
    risk_score: int
    risk_grade: str
    candidates: tuple[str, str]
    supported_cases: list[SimilarResolvedCaseDTO]
    inspected_cases: list[tuple[SimilarResolvedCaseDTO, ResolvedCaseDetailDTO]]
    selected_action: InvestigationActionDTO
    llm_call_count: int
    stop_reason: str
    investigation_result: InvestigationResultDTO


class DatabaseSimilarCaseTools:
    """기존 유사도 계산식과 PostgreSQL 조회를 연결한 Tool 구현."""

    def __init__(
        self,
        repository: AgentInvestigationRepository,
        *,
        similarity_config: CaseSimilarityConfig | None = None,
    ) -> None:
        self.repository = repository
        self.similarity_config = similarity_config or CaseSimilarityConfig()

    def search_similar_resolved_cases(
        self,
        *,
        current_case_id: str,
        candidate_fraud_types: tuple[str, str],
        type_scores: dict[str, float],
        evidence: list[RuleEvidenceDTO],
        risk_score: int,
        risk_grade: str,
        top_k: int = 5,
    ) -> list[SimilarResolvedCaseDTO]:
        rows = self.repository.list_resolved_cases(
            current_case_id=current_case_id,
            candidate_fraud_types=candidate_fraud_types,
        )
        current = CaseSimilarityFeatures(
            case_id=current_case_id,
            type_scores=type_scores,
            matched_components=_group_evidence(evidence),
            risk_score=risk_score,
            risk_grade=risk_grade,
        )
        candidates = [
            CaseSimilarityFeatures(
                case_id=case.case_id,
                type_scores=score.type_scores,
                matched_components=score.matched_components,
                risk_score=case.risk_score,
                risk_grade=case.risk_grade,
            )
            for case, score in rows
        ]
        ranked = rank_similar_cases(
            current,
            candidates,
            top_k=top_k,
            config=self.similarity_config,
        )
        return [
            SimilarResolvedCaseDTO(
                case_id=result.case_id,
                similarity_score=result.similarity_score,
                common_evidence_codes=result.common_evidence_codes,
            )
            for result in ranked
        ]

    def get_resolved_case_detail(self, case_id: str) -> ResolvedCaseDetailDTO:
        review = self.repository.get_resolved_review(case_id)
        if review is None:
            raise LookupError(f"완료 사건을 찾을 수 없다: {case_id}")
        return ResolvedCaseDetailDTO(
            case_id=case_id,
            confirmed_fraud_type=review.confirmed_fraud_type or "",
        )


class LimitedSimilarCaseInvestigator:
    """검색 결과를 관찰하며 상세조회 여부를 결정하는 제한된 조사 Agent."""

    def __init__(
        self,
        tools: SimilarCaseTools,
        action_selector: InvestigationActionSelector,
        *,
        top_k: int = 5,
        maximum_detail_calls: int = 2,
        maximum_llm_calls: int = 3,
        strong_similarity: float = 0.85,
        support_similarity: float = 0.75,
    ) -> None:
        self.tools = tools
        self.action_selector = action_selector
        self.top_k = top_k
        self.maximum_detail_calls = maximum_detail_calls
        self.maximum_llm_calls = maximum_llm_calls
        self.strong_similarity = strong_similarity
        self.support_similarity = support_similarity
        self.graph = self._build_graph()

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
        started_at = time.perf_counter()
        candidates = (confidence.top_type_code, confidence.second_type_code)
        final_state = self.graph.invoke(
            {
                "case_id": case_id,
                "rule_result": rule_result,
                "confidence": confidence,
                "risk_score": risk_score,
                "risk_grade": risk_grade,
                "candidates": candidates,
                "inspected_cases": [],
                "llm_call_count": 0,
            }
        )
        result = final_state["investigation_result"]
        if metrics is not None:
            llm_call_count = final_state.get("llm_call_count", 0)
            metrics.update(
                {
                    "investigation_latency_ms": round(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    "react_llm_call_count": llm_call_count,
                    # 현재 SDK 재시도 설정이 0회이므로 논리 호출과 실제 요청 수가 같다.
                    "api_attempt_count": llm_call_count,
                    "retry_count": 0,
                    "tool_call_count": 1
                    + len(final_state.get("inspected_cases", [])),
                    "fallback_used": (
                        result.investigation_status
                        is not InvestigationStatus.COMPLETED
                    ),
                    "fallback_reason": (
                        result.recommendation_reason
                        if result.investigation_status
                        is not InvestigationStatus.COMPLETED
                        else None
                    ),
                }
            )
        return result

    def _build_graph(self):
        graph = StateGraph(InvestigationGraphState)
        graph.add_node("search_cases", self._search_cases)
        graph.add_node("select_action", self._select_action)
        graph.add_node("inspect_case", self._inspect_case)
        graph.add_node("finalize_recommendation", self._finalize_recommendation)
        graph.add_node("finish_insufficient", self._finish_insufficient)

        graph.add_edge(START, "search_cases")
        graph.add_conditional_edges(
            "search_cases",
            lambda state: "select" if state.get("supported_cases") else "stop",
            {"select": "select_action", "stop": "finish_insufficient"},
        )
        graph.add_conditional_edges(
            "select_action",
            self._route_selected_action,
            {
                "inspect": "inspect_case",
                "recommend": "finalize_recommendation",
                "insufficient": "finish_insufficient",
            },
        )
        graph.add_conditional_edges(
            "inspect_case",
            lambda state: (
                "stop" if state.get("investigation_result") else "continue"
            ),
            {"stop": END, "continue": "select_action"},
        )
        graph.add_edge("finalize_recommendation", END)
        graph.add_edge("finish_insufficient", END)
        return graph.compile()

    def _search_cases(self, state: InvestigationGraphState) -> dict[str, object]:
        # Reason: 점수 차이가 작으므로 먼저 후보 유형의 완료 사건을 검색한다.
        similar_cases = self.tools.search_similar_resolved_cases(
            current_case_id=state["case_id"],
            candidate_fraud_types=state["candidates"],
            type_scores=state["rule_result"].type_scores,
            evidence=state["rule_result"].matched_components,
            risk_score=state["risk_score"],
            risk_grade=state["risk_grade"],
            top_k=self.top_k,
        )
        supported = [
            candidate
            for candidate in similar_cases
            if candidate.similarity_score >= self.support_similarity
            and candidate.common_evidence_codes
        ]
        reason = (
            "유사도 기준을 충족한 완료 사건이 없다."
            if not similar_cases
            else "공통 Rule 근거가 있는 완료 사건이 부족하다."
        )
        return {"supported_cases": supported, "stop_reason": reason}

    def _select_action(self, state: InvestigationGraphState) -> dict[str, object]:
        call_count = state["llm_call_count"]
        inspected = state["inspected_cases"]
        if call_count >= self.maximum_llm_calls:
            action = InvestigationActionDTO(
                action=(
                    InvestigationAction.STOP_RECOMMEND
                    if inspected
                    else InvestigationAction.STOP_INSUFFICIENT
                ),
                case_id=None,
                recommended_fraud_type=None,
                reason="LLM 호출 제한에 도달하여 확인된 근거로 조사를 종료했다.",
            )
            return {"selected_action": action}

        try:
            action = self.action_selector.select_action(
                candidate_fraud_types=state["candidates"],
                similar_cases=state["supported_cases"],
                inspected_cases=[detail for _candidate, detail in inspected],
                remaining_detail_calls=self.maximum_detail_calls - len(inspected),
            )
        except Exception:
            # LLM 장애가 고객 안내와 후속 대응 계획 전체를 막지 않도록 안전 종료한다.
            action = InvestigationActionDTO(
                action=InvestigationAction.STOP_INSUFFICIENT,
                case_id=None,
                recommended_fraud_type=None,
                reason="LLM 조사 판단 호출에 실패하여 Rule 1순위 유형을 유지한다.",
            )
        return {"selected_action": action, "llm_call_count": call_count + 1}

    def _inspect_case(self, state: InvestigationGraphState) -> dict[str, object]:
        inspected = list(state["inspected_cases"])
        if len(inspected) >= self.maximum_detail_calls:
            return {
                "investigation_result": self._insufficient(
                    state["confidence"], "사건 상세조회 제한에 도달했다."
                )
            }
        selected_id = state["selected_action"].case_id
        selected = next(
            (
                candidate
                for candidate in state["supported_cases"]
                if candidate.case_id == selected_id
                and all(
                    inspected_candidate.case_id != candidate.case_id
                    for inspected_candidate, _detail in inspected
                )
            ),
            None,
        )
        if selected is None:
            return {
                "investigation_result": self._insufficient(
                    state["confidence"], "LLM이 허용되지 않은 사건을 선택했다."
                )
            }
        # Observation: 선택한 사건의 담당자 확정 유형을 State에 반영한다.
        inspected.append(
            (selected, self.tools.get_resolved_case_detail(selected.case_id))
        )
        return {"inspected_cases": inspected}

    def _finalize_recommendation(
        self,
        state: InvestigationGraphState,
    ) -> dict[str, object]:
        action = state["selected_action"]
        return {
            "investigation_result": self._build_recommendation(
                confidence=state["confidence"],
                candidates=state["candidates"],
                inspected=state["inspected_cases"],
                requested_type=action.recommended_fraud_type,
                reason=action.reason,
            )
        }

    def _finish_insufficient(
        self,
        state: InvestigationGraphState,
    ) -> dict[str, object]:
        action = state.get("selected_action")
        reason = action.reason if action is not None else state["stop_reason"]
        return {
            "investigation_result": self._insufficient(
                state["confidence"], reason
            )
        }

    @staticmethod
    def _route_selected_action(state: InvestigationGraphState) -> str:
        action = state["selected_action"].action
        if action is InvestigationAction.INSPECT_CASE:
            return "inspect"
        if action is InvestigationAction.STOP_RECOMMEND:
            return "recommend"
        return "insufficient"

    def _build_recommendation(
        self,
        *,
        confidence: TypeConfidenceResult,
        candidates: tuple[str, str],
        inspected: list[tuple[SimilarResolvedCaseDTO, ResolvedCaseDetailDTO]],
        requested_type: str | None,
        reason: str,
    ) -> InvestigationResultDTO:
        if not inspected:
            return self._insufficient(confidence, "상세조회한 완료 사건이 없다.")

        # 상세조회에서 담당자의 확정 유형과 실제 처리 결과가 확인된 사건만 집계한다.
        support_count = Counter(detail.confirmed_fraud_type for _case, detail in inspected)
        support_score: dict[str, float] = defaultdict(float)
        for candidate, detail in inspected:
            support_score[detail.confirmed_fraud_type] += candidate.similarity_score
        recommended_type = max(
            candidates,
            key=lambda type_code: (support_count[type_code], support_score[type_code]),
        )
        if requested_type is not None and requested_type != recommended_type:
            return self._insufficient(confidence, "LLM 추천 유형이 확인된 근거와 일치하지 않는다.")
        best = next(
            candidate
            for candidate, detail in inspected
            if detail.confirmed_fraud_type == recommended_type
        )
        enough = support_count[recommended_type] >= 2 or (
            best.similarity_score >= self.strong_similarity
        )
        if not enough:
            return self._insufficient(confidence, "한 유형을 우선 추천할 만큼 과거 확정 근거가 충분하지 않다.")

        return InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=confidence.score_margin,
            investigation_status=InvestigationStatus.COMPLETED,
            recommended_fraud_type=recommended_type,
            recommendation_reason=(
                f"{reason} 공통 Rule 근거가 있는 유사 완료 사건 "
                f"{support_count[recommended_type]}건이 {recommended_type} 유형으로 확정되었다."
            ),
            best_similarity_score=best.similarity_score,
            common_evidence_codes=list(best.common_evidence_codes),
            confirmed_case_count=support_count[recommended_type],
        )

    @staticmethod
    def _insufficient(
        confidence: TypeConfidenceResult,
        reason: str,
    ) -> InvestigationResultDTO:
        return InvestigationResultDTO(
            classification_status=ClassificationStatus.AMBIGUOUS,
            score_margin=confidence.score_margin,
            investigation_status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
            recommended_fraud_type=None,
            recommendation_reason=reason,
            best_similarity_score=None,
            common_evidence_codes=[],
            confirmed_case_count=0,
        )


def _group_evidence(
    evidence: list[RuleEvidenceDTO],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        grouped[item.fraud_type].append(item.evidence_code)
    return dict(grouped)


__all__ = [
    "DatabaseSimilarCaseTools",
    "InvestigationActionOutput",
    "InvestigationGraphState",
    "InvestigationActionSelector",
    "LimitedSimilarCaseInvestigator",
    "OpenAIInvestigationActionSelector",
    "SimilarCaseTools",
]
