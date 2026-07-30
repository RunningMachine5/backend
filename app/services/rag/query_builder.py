from app.dto.agent import RagQueryDTO
from app.dto.fraud import FraudAssessmentDTO


class RagQueryBuilder:
    """최종 탐지 결과를 검색 가능한 자연어 질의로 변환한다."""

    def build(self, assessment: FraudAssessmentDTO) -> RagQueryDTO:
        """근거, 사기유형, 위험등급을 담당자용 검색 질의로 조합한다."""
        evidence_text = ", ".join(assessment.evidence)
        # 검색 정확도를 높일 수 있도록 탐지 결과와 검색 목적을 하나의 문장에 포함한다.
        query = (
            f"위험등급 {assessment.risk_grade.value}, "
            f"대표 사기유형 {assessment.primary_fraud_type.value}, "
            f"탐지 근거 {evidence_text}인 금융 이상거래의 "
            "유사 사례와 모니터링 담당자 대응 절차를 검색한다."
        )
        return RagQueryDTO(
            risk_grade=assessment.risk_grade,
            fraud_type=assessment.primary_fraud_type,
            evidence=assessment.evidence,
            query=query,
        )

