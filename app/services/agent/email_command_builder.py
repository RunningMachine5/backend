"""유형 확실성 판단 결과를 자동 이메일 발송 명령으로 변환한다."""

from __future__ import annotations

from app.domain.agent_status import ClassificationStatus, InvestigationStatus
from app.dto.agent import (
    FraudAlertEmailCommand,
    InvestigationResultDTO,
)
from app.services.agent.type_confidence import TypeConfidenceResult


def build_fraud_alert_email_command(
    *,
    transaction_id: str,
    type_confidence: TypeConfidenceResult,
    investigation_result: InvestigationResultDTO | None = None,
) -> FraudAlertEmailCommand:
    """Rule 상위 후보와 선택적인 Agent 추천으로 이메일 표시 순서를 결정한다."""

    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("transaction_id는 비어 있지 않은 문자열이어야 한다.")

    primary_type = type_confidence.top_type_code
    secondary_type = type_confidence.second_type_code
    classification_status = type_confidence.classification_status

    if investigation_result is not None:
        classification_status = investigation_result.classification_status
        recommended_type = investigation_result.recommended_fraud_type
        top_candidates = {primary_type, secondary_type}
        # 조사 Agent는 Rule 상위 후보의 순서만 보강하며 새로운 유형을 끼워 넣지 않는다.
        if (
            classification_status is ClassificationStatus.AMBIGUOUS
            and investigation_result.investigation_status
            is InvestigationStatus.COMPLETED
            and recommended_type in top_candidates
            and recommended_type != primary_type
        ):
            primary_type, secondary_type = secondary_type, primary_type

    return FraudAlertEmailCommand(
        transaction_id=transaction_id,
        primary_suspected_type=primary_type,
        secondary_suspected_type=secondary_type,
        classification_status=classification_status,
    )


__all__ = ["build_fraud_alert_email_command"]
