"""모든 이상거래에 표시할 유사 완료 사건 상위 3개를 생성한다."""

from __future__ import annotations

from app.dto.agent import FraudTypeScoreResultDTO, SimilarCaseResultDTO
from app.services.agent.similar_case_investigator import SimilarCaseTools


class DashboardSimilarCaseService:
    """완료·검토된 과거 사건을 조회하여 대시보드용 순위를 만든다."""

    def __init__(self, tools: SimilarCaseTools) -> None:
        self.tools = tools

    def find_top_three(
        self,
        *,
        current_case_id: str,
        rule_result: FraudTypeScoreResultDTO,
        risk_score: int,
        risk_grade: str,
    ) -> list[SimilarCaseResultDTO]:
        cases = self.tools.search_similar_resolved_cases(
            current_case_id=current_case_id,
            candidate_fraud_types=tuple(rule_result.type_scores),
            type_scores=rule_result.type_scores,
            evidence=rule_result.matched_components,
            risk_score=risk_score,
            risk_grade=risk_grade,
            top_k=3,
        )
        return [
            SimilarCaseResultDTO(
                similar_case_id=case.case_id,
                similarity_rank=rank,
                similarity_score=case.similarity_score,
                similarity_reason=_build_similarity_reason(
                    case.common_evidence_codes
                ),
            )
            for rank, case in enumerate(cases, start=1)
        ]


def _build_similarity_reason(common_evidence_codes: tuple[str, ...]) -> str:
    if common_evidence_codes:
        evidence = ", ".join(
            code.split(":", 1)[-1] for code in common_evidence_codes[:3]
        )
        return f"공통 Rule 근거({evidence})와 유형 점수·위험도가 유사함"
    return "유형 점수 분포와 위험점수·위험등급이 유사함"


__all__ = ["DashboardSimilarCaseService"]
