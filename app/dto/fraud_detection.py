"""doo 거래 응답에 운영 탐지 결과를 덧붙인다."""

from datetime import datetime

from app.dto.transaction import TransactionResponseDTO


class FraudDetectionResponseDTO(TransactionResponseDTO):
    """거래 응답과 ML·룰·라벨 결과를 함께 반환한다."""

    # doo의 TransactionResponseDTO를 직접 바꾸지 않도록 상속해서 확장한다.
    predict_result: bool | None = None
    rule_set_id: int | None = None
    rule_scores: dict[str, float] | None = None

    # 담당자가 나중에 확정한 정답 라벨은 거래 직후에는 없을 수 있다.
    confirmed_is_fraud: bool | None = None
    labeled_at: datetime | None = None
    transaction_amount: int
    transaction_datetime: datetime
    risk_score: float | None = None
    created_at: datetime
    # 서버가 거래 요청을 저장한 시각이다. 대시보드 실시간 그래프와 같은 기준을 쓴다.
    received_at: datetime


__all__ = ["FraudDetectionResponseDTO"]
