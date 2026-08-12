"""유형이 애매한 사건에 필요한 과거 완료 사건만 선택적으로 조사한다."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Protocol

from app.domain.agent_status import ClassificationStatus, InvestigationStatus
from app.dto.agent import (
    FraudTypeScoreResultDTO,
    InvestigationResultDTO,
    RuleEvidenceDTO,
)
from app.dto.agent_investigation import (
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
        *,
        top_k: int = 5,
        maximum_detail_calls: int = 2,
        strong_similarity: float = 0.85,
        support_similarity: float = 0.75,
    ) -> None:
        self.tools = tools
        self.top_k = top_k
        self.maximum_detail_calls = maximum_detail_calls
        self.strong_similarity = strong_similarity
        self.support_similarity = support_similarity

    def investigate(
        self,
        *,
        case_id: str,
        rule_result: FraudTypeScoreResultDTO,
        confidence: TypeConfidenceResult,
        risk_score: int,
        risk_grade: str,
    ) -> InvestigationResultDTO:
        candidates = (confidence.top_type_code, confidence.second_type_code)

        # Reason: 점수 차이가 작으므로 먼저 후보 유형의 완료 사건을 검색한다.
        similar_cases = self.tools.search_similar_resolved_cases(
            current_case_id=case_id,
            candidate_fraud_types=candidates,
            type_scores=rule_result.type_scores,
            evidence=rule_result.matched_components,
            risk_score=risk_score,
            risk_grade=risk_grade,
            top_k=self.top_k,
        )
        if not similar_cases:
            return self._insufficient(confidence, "유사도 기준을 충족한 완료 사건이 없다.")

        # Observation: 유사도가 높은 사건부터 필요한 수만 상세 처리 결과를 확인한다.
        supported = [
            candidate
            for candidate in similar_cases
            if candidate.similarity_score >= self.support_similarity
            and candidate.common_evidence_codes
        ]
        inspected = [
            (candidate, self.tools.get_resolved_case_detail(candidate.case_id))
            for candidate in supported[: self.maximum_detail_calls]
        ]
        if not inspected:
            return self._insufficient(confidence, "공통 Rule 근거가 있는 완료 사건이 부족하다.")

        # 상세조회에서 담당자의 확정 유형과 실제 처리 결과가 확인된 사건만 집계한다.
        support_count = Counter(detail.confirmed_fraud_type for _case, detail in inspected)
        support_score: dict[str, float] = defaultdict(float)
        for candidate, detail in inspected:
            support_score[detail.confirmed_fraud_type] += candidate.similarity_score
        recommended_type = max(
            candidates,
            key=lambda type_code: (support_count[type_code], support_score[type_code]),
        )
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
                f"공통 Rule 근거가 있는 유사 완료 사건 {support_count[recommended_type]}건이 "
                f"{recommended_type} 유형으로 확정되었다."
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
    "LimitedSimilarCaseInvestigator",
    "SimilarCaseTools",
]
