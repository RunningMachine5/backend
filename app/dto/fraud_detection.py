"""doo 거래 응답에 운영 탐지 결과를 덧붙인다."""

from datetime import datetime

from app.dto.transaction import TransactionResponseDTO


class FraudDetectionResponseDTO(TransactionResponseDTO):
    """거래 응답과 ML·룰·라벨 결과를 함께 반환한다."""

    predict_result: bool | None = None
    rule_set_id: int | None = None
    rule_scores: dict[str, float] | None = None
    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None
    created_at: datetime


__all__ = ["FraudDetectionResponseDTO"]
