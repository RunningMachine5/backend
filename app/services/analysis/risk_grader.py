from app.domain.enums import RiskGrade


class RiskGrader:
    """대표 사기유형 점수를 사람이 읽는 위험등급으로 변환한다."""

    def grade(self, fraud_type_score: float) -> RiskGrade:
        """유형 점수 임계값에 따라 네 단계 위험등급을 반환한다."""
        if fraud_type_score >= 0.85:
            return RiskGrade.VERY_HIGH
        if fraud_type_score >= 0.65:
            return RiskGrade.HIGH
        if fraud_type_score >= 0.40:
            return RiskGrade.MEDIUM
        return RiskGrade.LOW

