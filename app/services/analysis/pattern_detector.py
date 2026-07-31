from app.dto.fraud import PatternScoreDTO
from app.dto.transaction import TransactionFeaturesDTO


class PatternDetector:
    """거래 컬럼에서 맥락에 필요한 이상패턴 점수를 추출한다."""

    def detect(self, transfeat: TransactionFeaturesDTO) -> list[PatternScoreDTO]:
        """고액, 사용자 평소 대비 편차, 카드/업종 조합 패턴을 계산한다."""
        #객체의 유관 피쳐 추출
        amount = transfeat.high_relevance_feature.get("amount")
        deviation_ratio = transfeat.high_relevance_feature.get("deviation_ratio")
        merchant_category = transfeat.high_relevance_feature.get("merchant_category")
        payment_method = transfeat.high_relevance_feature.get("payment_method")

        #패턴 별 점수 로직(예시)
        high_amount_score = min(amount / 50_000_000, 1.0)
        user_deviation_score = min(deviation_ratio / 100, 1.0)
        risky_merchant_score = (
            1.0
            if payment_method == "CARD"
            and merchant_category == "VEHICLES"
            else 0.2
        )

        #사기 유형 분류를 위한 이상 패턴 별 점수 반환
        return [
            PatternScoreDTO(
                pattern_name="고액 거래",
                score=round(high_amount_score, 2),
                evidence=f"거래금액 {amount:,}원이 고액 기준과 비교해 비정상적임",
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
                    f"{payment_method} 결제가 "
                    f"{merchant_category} 업종에서 발생함"
                ),
            ),
        ]

