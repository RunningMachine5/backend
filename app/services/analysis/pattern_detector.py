from app.dto.fraud import PatternScoreDTO
from app.dto.transaction import TransactionDTO


class PatternDetector:
    """거래 컬럼에서 맥락에 필요한 이상패턴 점수를 추출한다."""

    def detect(self, transaction: TransactionDTO) -> list[PatternScoreDTO]:
        """고액, 사용자 평소 대비 편차, 카드/업종 조합 패턴을 계산한다."""
        deviation_ratio = transaction.amount / transaction.user_amount_std_dev
        high_amount_score = min(transaction.amount / 50_000_000, 1.0)
        user_deviation_score = min(deviation_ratio / 100, 1.0)
        risky_merchant_score = (
            1.0
            if transaction.payment_method == "CARD"
            and transaction.merchant_category == "VEHICLES"
            else 0.2
        )

        return [
            PatternScoreDTO(
                pattern_name="고액 거래",
                score=round(high_amount_score, 2),
                evidence=f"거래금액 {transaction.amount:,}원이 고액 기준과 비교해 비정상적임",
            ),
            PatternScoreDTO(
                pattern_name="사용자 평소 금액 이탈",
                score=round(user_deviation_score, 2),
                evidence=f"거래금액이 사용자 표준편차의 약 {deviation_ratio:.1f}배임",
            ),
            PatternScoreDTO(
                pattern_name="카드-고위험 업종 조합",
                score=round(risky_merchant_score, 2),
                evidence=(
                    f"{transaction.payment_method} 결제가 "
                    f"{transaction.merchant_category} 업종에서 발생함"
                ),
            ),
        ]

