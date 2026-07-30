from app.domain.enums import RiskGrade
from app.dto.fraud import FraudAssessmentDTO
from app.dto.transaction import TransactionDTO
from app.services.analysis.fraud_type_scorer import FraudTypeScorer
from app.services.analysis.pattern_detector import PatternDetector
from app.services.analysis.risk_grader import RiskGrader
from app.services.classification.fake_fraud_model import FakeFraudModel


class FraudDetectionPipeline:
    """분류부터 위험등급까지의 머신러닝 기반 사기 분석 흐름을 조정한다."""

    def __init__(self) -> None:
        """파이프라인에서 사용할 Fake 모델과 분석 서비스를 준비한다."""
        self.model = FakeFraudModel()
        self.pattern_detector = PatternDetector()
        self.fraud_type_scorer = FraudTypeScorer()
        self.risk_grader = RiskGrader()

    def run(self, transaction: TransactionDTO) -> FraudAssessmentDTO:
        """거래 한 건을 분류하고 패턴, 사기유형, 위험등급을 차례로 계산한다."""
        prediction = self.model.predict(transaction)
        if not prediction.is_fraud:
            return FraudAssessmentDTO(
                transaction=transaction,
                prediction=prediction,
                patterns=[],
                fraud_type_scores=[],
                primary_fraud_type=None,
                risk_grade=RiskGrade.LOW,
                evidence=["Fake 모델의 사기 분류 임계값 0.55 미만"],
            )

        patterns = self.pattern_detector.detect(transaction)
        fraud_type_scores = self.fraud_type_scorer.score(patterns)
        primary_score = max(fraud_type_scores, key=lambda item: item.score)
        risk_grade = self.risk_grader.grade(primary_score.score)

        evidence = [
            pattern.evidence
            for pattern in patterns
            if pattern.score >= 0.40
        ]
        return FraudAssessmentDTO(
            transaction=transaction,
            prediction=prediction,
            patterns=patterns,
            fraud_type_scores=fraud_type_scores,
            primary_fraud_type=primary_score.fraud_type,
            risk_grade=risk_grade,
            evidence=evidence,
        )
