"""Agent 관련 ORM 모델과 중첩 DTO 사이의 변환을 담당한다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from app.data.model.agent import AgentCase
from app.data.model.fraud_rule import (
    FraudRule,
    FraudRuleComponent,
    FraudTypeScoreResult,
)
from app.data.model.transaction import Transaction
from app.domain.agent_status import (
    AgentExecutionStatus,
    ClassificationStatus,
    InformationStatus,
    InvestigationStatus,
    RuleFilterStatus,
)
from app.domain.enums import RiskGrade
from app.dto.agent import (
    AgentInputDTO,
    AgentResponseDTO,
    ChecklistItemDTO,
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    RecommendedActionDTO,
    ResponsePlanDTO,
    RuleEvidenceDTO,
    SimilarCaseResultDTO,
)


def to_fraud_type_score_result_dto(
    score_result: FraudTypeScoreResult,
    *,
    rules: Iterable[FraudRule],
    components: Iterable[FraudRuleComponent],
) -> FraudTypeScoreResultDTO:
    """저장된 유형 점수와 Rule 정의를 설명 가능한 DTO로 변환한다."""

    if score_result.id is None:
        raise ValueError("저장되지 않은 Rule 결과는 Agent 입력으로 사용할 수 없다.")

    rule_types = {
        rule.id: rule.type_code
        for rule in rules
        if rule.id is not None and rule.rule_set_id == score_result.rule_set_id
    }
    component_weights = {
        (rule_types[component.rule_id], component.component_key): component.weight
        for component in components
        if component.rule_id in rule_types
    }

    evidence: list[RuleEvidenceDTO] = []
    for fraud_type, component_keys in score_result.matched_components.items():
        for component_key in component_keys:
            weight_key = (fraud_type, component_key)
            if weight_key not in component_weights:
                raise ValueError(
                    "적중한 Rule 구성요소의 가중치를 찾을 수 없다: "
                    f"{fraud_type}/{component_key}"
                )
            evidence.append(
                RuleEvidenceDTO(
                    fraud_type=fraud_type,
                    evidence_code=component_key,
                    # 현재 저장 계약에서는 조건이 적중했다는 사실을 관찰값으로 사용한다.
                    observed_value=True,
                    contribution=float(component_weights[weight_key]),
                )
            )

    return FraudTypeScoreResultDTO(
        fraud_type_score_result_id=score_result.id,
        rule_filter_status=RuleFilterStatus(score_result.rule_filter_status),
        primary_fraud_type=score_result.primary_fraud_type,
        type_scores={
            fraud_type: float(score)
            for fraud_type, score in score_result.type_scores.items()
        },
        matched_components=evidence,
    )


def build_agent_input_dto(
    transaction: Transaction,
    score_result: FraudTypeScoreResult,
    *,
    risk_score: int,
    risk_grade: RiskGrade | str,
) -> AgentInputDTO:
    """동일 거래의 Rule 결과와 위험등급을 Agent 실행 입력으로 묶는다."""

    if transaction.transaction_id != score_result.transaction_id:
        raise ValueError("거래와 Rule 결과의 transaction_id가 일치하지 않는다.")
    if score_result.id is None:
        raise ValueError("저장되지 않은 Rule 결과는 Agent 입력으로 사용할 수 없다.")
    if isinstance(risk_score, bool) or not isinstance(risk_score, int):
        raise TypeError("risk_score는 정수여야 한다.")
    if not 0 <= risk_score <= 100:
        raise ValueError("risk_score는 0~100 범위여야 한다.")

    normalized_grade = (
        risk_grade if isinstance(risk_grade, RiskGrade) else RiskGrade(risk_grade)
    )
    return AgentInputDTO(
        transaction_id=transaction.transaction_id,
        fraud_type_score_result_id=score_result.id,
        risk_score=risk_score,
        risk_grade=normalized_grade,
    )


def to_agent_response_dto(
    agent_case: AgentCase,
    score_result: FraudTypeScoreResult,
    *,
    rules: Iterable[FraudRule],
    components: Iterable[FraudRuleComponent],
) -> AgentResponseDTO:
    """Agent 사건과 연결된 Rule 결과를 대시보드 응답 DTO로 조합한다."""

    if score_result.id != agent_case.fraud_type_score_result_id:
        raise ValueError("Agent 사건과 Rule 결과 식별자가 일치하지 않는다.")
    if score_result.transaction_id != agent_case.transaction_id:
        raise ValueError("Agent 사건과 Rule 결과의 transaction_id가 일치하지 않는다.")

    return AgentResponseDTO(
        case_id=agent_case.case_id,
        transaction_id=agent_case.transaction_id,
        execution_status=AgentExecutionStatus(agent_case.execution_status),
        failure_reason=agent_case.failure_reason,
        rule_result=to_fraud_type_score_result_dto(
            score_result,
            rules=rules,
            components=components,
        ),
        risk_score=agent_case.risk_score,
        risk_grade=RiskGrade(agent_case.risk_grade),
        investigation_result=investigation_result_from_json(
            agent_case.investigation_result
        ),
        best_similar_case_id=agent_case.best_similar_case_id,
        similar_case_results=similar_case_results_from_json(
            agent_case.similar_case_results
        ),
        response_result=response_plan_from_json(agent_case.response_result),
        generation_metadata=dict(agent_case.generation_metadata or {}),
        created_at=agent_case.created_at,
        completed_at=agent_case.completed_at,
    )


def investigation_result_from_json(
    payload: Mapping[str, Any] | None,
) -> InvestigationResultDTO | None:
    """investigation_result JSONB를 조사 결과 DTO로 복원한다."""

    if payload is None:
        return None
    return InvestigationResultDTO(
        classification_status=ClassificationStatus(payload["classification_status"]),
        score_margin=float(payload["score_margin"]),
        investigation_status=InvestigationStatus(payload["investigation_status"]),
        recommended_fraud_type=payload.get("recommended_fraud_type"),
        recommendation_reason=payload.get("recommendation_reason"),
        best_similarity_score=(
            float(payload["best_similarity_score"])
            if payload.get("best_similarity_score") is not None
            else None
        ),
        common_evidence_codes=list(payload.get("common_evidence_codes", [])),
        confirmed_case_count=int(payload.get("confirmed_case_count", 0)),
    )


def similar_case_results_from_json(
    payload: Iterable[Mapping[str, Any]] | None,
) -> list[SimilarCaseResultDTO]:
    """similar_case_results JSONB 배열을 순위가 보존된 DTO 목록으로 복원한다."""

    if payload is None:
        return []
    return [
        SimilarCaseResultDTO(
            similar_case_id=item["similar_case_id"],
            similarity_rank=int(item["similarity_rank"]),
            similarity_score=float(item["similarity_score"]),
            similarity_reason=item["similarity_reason"],
        )
        for item in payload
    ]


def response_plan_from_json(
    payload: Mapping[str, Any] | None,
) -> ResponsePlanDTO | None:
    """response_result JSONB를 권장 조치와 체크리스트가 포함된 DTO로 복원한다."""

    if payload is None:
        return None

    actions = [
        RecommendedActionDTO(
            priority=int(action["priority"]),
            action_code=action["action_code"],
            action=action["action"],
            reason=action["reason"],
            required=bool(action["required"]),
            procedure_steps=list(action.get("procedure_steps", [])),
            cautions=list(action.get("cautions", [])),
        )
        for action in payload.get("recommended_actions", [])
    ]
    checklist = [
        ChecklistItemDTO(
            item_code=item["item_code"],
            label=item["label"],
            required=bool(item["required"]),
        )
        for item in payload.get("checklist", [])
    ]
    return ResponsePlanDTO(
        applied_fraud_type=payload["applied_fraud_type"],
        information_status=InformationStatus(payload["information_status"]),
        summary=payload["summary"],
        recommended_actions=actions,
        checklist=checklist,
    )


def agent_response_from_dict(payload: Mapping[str, Any]) -> AgentResponseDTO:
    """직렬화된 Agent 응답을 동일한 중첩 DTO 구조로 복원한다."""

    rule_payload = payload["rule_result"]
    rule_result = FraudTypeScoreResultDTO(
        fraud_type_score_result_id=int(rule_payload["fraud_type_score_result_id"]),
        rule_filter_status=RuleFilterStatus(rule_payload["rule_filter_status"]),
        primary_fraud_type=rule_payload.get("primary_fraud_type"),
        type_scores={
            fraud_type: float(score)
            for fraud_type, score in rule_payload["type_scores"].items()
        },
        matched_components=[
            RuleEvidenceDTO(
                fraud_type=item["fraud_type"],
                evidence_code=item["evidence_code"],
                observed_value=item.get("observed_value"),
                contribution=float(item["contribution"]),
            )
            for item in rule_payload.get("matched_components", [])
        ],
    )
    return AgentResponseDTO(
        case_id=payload["case_id"],
        transaction_id=payload["transaction_id"],
        execution_status=AgentExecutionStatus(payload["execution_status"]),
        failure_reason=payload.get("failure_reason"),
        rule_result=rule_result,
        risk_score=int(payload["risk_score"]),
        risk_grade=RiskGrade(payload["risk_grade"]),
        investigation_result=investigation_result_from_json(
            payload.get("investigation_result")
        ),
        best_similar_case_id=payload.get("best_similar_case_id"),
        similar_case_results=similar_case_results_from_json(
            payload.get("similar_case_results")
        ),
        response_result=response_plan_from_json(payload.get("response_result")),
        generation_metadata=dict(payload.get("generation_metadata", {})),
        created_at=_parse_datetime(payload["created_at"]),
        completed_at=(
            _parse_datetime(payload["completed_at"])
            if payload.get("completed_at") is not None
            else None
        ),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


__all__ = [
    "agent_response_from_dict",
    "build_agent_input_dto",
    "investigation_result_from_json",
    "response_plan_from_json",
    "similar_case_results_from_json",
    "to_agent_response_dto",
    "to_fraud_type_score_result_dto",
]
