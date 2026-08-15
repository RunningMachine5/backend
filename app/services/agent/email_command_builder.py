"""유형 확실성 판단 결과를 자동 이메일 발송 명령으로 변환한다."""

from __future__ import annotations

from app.dto.agent import FraudAlertEmailCommand
from app.services.agent.type_confidence import TypeConfidenceResult


def build_fraud_alert_email_command(
    *,
    transaction_id: int,
    type_confidence: TypeConfidenceResult,
) -> FraudAlertEmailCommand:
    """Rule Engine의 원본 1·2순위 유형으로 이메일 명령을 생성한다."""

    if (
        not isinstance(transaction_id, int)
        or isinstance(transaction_id, bool)
        or transaction_id <= 0
    ):
        raise ValueError("transaction_id는 양의 정수여야 한다.")

    return FraudAlertEmailCommand(
        transaction_id=transaction_id,
        primary_suspected_type=type_confidence.top_type_code,
        secondary_suspected_type=type_confidence.second_type_code,
        classification_status=type_confidence.classification_status,
    )


__all__ = ["build_fraud_alert_email_command"]
