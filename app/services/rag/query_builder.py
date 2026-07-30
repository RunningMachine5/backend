from app.dto.agent import RagQueryDTO
from app.dto.fraud import FraudAssessmentDTO


class RagQueryBuilder:
    """최종 탐지 결과를 검색 가능한 자연어 질의로 변환한다."""

    def build(self, assessment: FraudAssessmentDTO) -> RagQueryDTO:
        """근거, 사기유형, 위험등급을 질문 예시 형식으로 조합한다."""
        evidence_text = ", ".join(assessment.evidence)
        query = (
            f"{evidence_text} 사기패턴들이 발견되었어. "
            f"{assessment.primary_fraud_type.value}에 대해 어떻게 대처하면 좋을까?"
        )
        return RagQueryDTO(
            risk_grade=assessment.risk_grade,
            fraud_type=assessment.primary_fraud_type,
            evidence=assessment.evidence,
            query=query,
        )

