from dataclasses import dataclass

from app.domain.enums import FraudType, RiskGrade
from app.dto.transaction import TransactionDTO


@dataclass(frozen=True)
class FraudPredictionDTO:
    """Fake 머신러닝 이진 분류 결과 DTO."""

    is_fraud: bool
    fraud_probability: float


@dataclass(frozen=True)
class PatternScoreDTO:
    """한 가지 이상패턴의 점수와 판단 근거 DTO."""

    pattern_name: str
    score: float
    evidence: str


@dataclass(frozen=True)
class FraudTypeScoreDTO:
    """이상패턴 가중합으로 계산한 사기유형별 점수 DTO."""

    fraud_type: FraudType
    score: float


@dataclass(frozen=True)
class FraudAssessmentDTO:
    """분류, 패턴, 유형 점수, 위험등급을 합친 최종 탐지 결과 DTO."""

    transaction: TransactionDTO
    prediction: FraudPredictionDTO
    patterns: list[PatternScoreDTO]
    fraud_type_scores: list[FraudTypeScoreDTO]
    primary_fraud_type: FraudType | None
    risk_grade: RiskGrade
    evidence: list[str]
