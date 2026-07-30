from app.domain.enums import FraudType
from app.dto.fraud import FraudTypeScoreDTO, PatternScoreDTO


class FraudTypeScorer:
    """이상패턴 점수의 가중합으로 사기유형별 점수를 계산한다."""

    def score(self, patterns: list[PatternScoreDTO]) -> list[FraudTypeScoreDTO]:
        """정해진 패턴 순서와 유형별 가중치를 사용해 세 유형을 점수화한다."""
        high_amount = patterns[0].score
        user_deviation = patterns[1].score
        risky_merchant = patterns[2].score

        large_payment = (
            high_amount * 0.45 + user_deviation * 0.35 + risky_merchant * 0.20
        )
        stolen_card = (
            high_amount * 0.20 + user_deviation * 0.30 + risky_merchant * 0.50
        )
        unusual_behavior = (
            high_amount * 0.20 + user_deviation * 0.60 + risky_merchant * 0.20
        )

        return [
            FraudTypeScoreDTO(
                fraud_type=FraudType.LARGE_AMOUNT_PAYMENT,
                score=round(large_payment, 2),
            ),
            FraudTypeScoreDTO(
                fraud_type=FraudType.STOLEN_CARD_PAYMENT,
                score=round(stolen_card, 2),
            ),
            FraudTypeScoreDTO(
                fraud_type=FraudType.UNUSUAL_USER_BEHAVIOR,
                score=round(unusual_behavior, 2),
            ),
        ]

