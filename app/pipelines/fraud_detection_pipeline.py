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
        prediction = self.model.predict(transaction) # 거래의 이상 여부 예측 -> 예측여부, 예측 가능성 리턴
        if not prediction.is_fraud:
            return FraudAssessmentDTO(
                transaction=transaction,
                prediction=prediction,
                patterns=[],
                fraud_type_scores=[],
                primary_fraud_type=None,
                risk_score=None,
                risk_grade=None,
                amount_risk_factor=None,
                amount_points=None,
                ml_probability_points=None,
                evidence=["사기 분류 임계값 0.55 미만"],
            )

        # 거래 데이터 그대로 받음 -> 기존 소비 패턴보다 큰 거래액, 고액 거래, 카드/고위험 영역 거래 의 점수를 매김 -> 3가지 패턴 리스트 리턴(모든 패턴에 대한 점수를 리턴)
        patterns = self.pattern_detector.detect(prediction)
        #패턴 점수를 조합해서 사기 유형 판단. (고액, 카드 도난, 이상 행동)
        fraud_type_scores = self.fraud_type_scorer.score(patterns)
        # 가장 점수가 높은 유형을 대표 사기유형으로 선택한다.
        primary_score = max(fraud_type_scores, key=lambda item: item.score)

        # Rule 점수는 유형 분류에만 사용하고 위험도는 거래금액과 ML 확률로 계산한다.
        risk_assessment = self.risk_grader.assess(
            transaction_amount=transaction.amount,
            fraud_probability=prediction.fraud_probability,
        )

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
            risk_score=risk_assessment.risk_score,
            risk_grade=risk_assessment.risk_grade,
            amount_risk_factor=risk_assessment.amount_risk_factor,
            amount_points=risk_assessment.amount_points,
            ml_probability_points=risk_assessment.ml_probability_points,
            evidence=evidence,
        )
