"""거래 저장 직후 대시보드가 부분 갱신할 SSE payload를 만든다."""

from typing import Any

from app.domain.agent_status import RuleFilterStatus
from app.dto.agent import AgentInputDTO
from app.services.transaction.detection_result_service import FraudDetectionResult


def build_transaction_dashboard_event(
    *,
    source: str,
    result: FraudDetectionResult,
    agent_input: AgentInputDTO | None,
) -> dict[str, Any]:
    """전체 재조회 없이 새 거래를 반영할 JSON-safe 데이터를 만든다."""

    transaction = result.transaction
    transaction_id = result.response.transaction_id
    suspicious_case = None
    if result.response.predict_result is True:
        agent_started = agent_input is not None
        suspicious_case = {
            "case_id": f"UNASSIGNED-{transaction_id}",
            "transaction_id": transaction_id,
            "execution_status": (
                "PROCESSING" if agent_started else "NOT_AVAILABLE"
            ),
            "risk_score": agent_input.risk_score if agent_input else None,
            "risk_grade": (
                agent_input.risk_grade.value if agent_input else None
            ),
            "primary_fraud_type": (
                result.score_result.primary_fraud_type
                if result.score_result is not None
                else None
            ),
            "transaction_amount": transaction.transaction_amount,
            "transaction_datetime": transaction.transaction_datetime.isoformat(),
            "received_at": result.response.received_at.isoformat(),
            "ip_address": (
                str(transaction.ip_address)
                if transaction.ip_address is not None
                else None
            ),
            "review_status": (
                "PROCESSING" if agent_started else "NOT_AVAILABLE"
            ),
        }

    overview_suspicious = (
        result.score_result is not None
        and result.score_result.rule_filter_status == RuleFilterStatus.APPLIED.value
    )

    return {
        "source": source,
        "transaction_patch": {
            "event_id": f"transaction:{transaction_id}",
            "transaction": result.response.model_dump(mode="json"),
            "channel": transaction.channel,
            "date_label": (
                f"{transaction.transaction_datetime.month}/"
                f"{transaction.transaction_datetime.day}"
            ),
            "rule_analysis_completed": result.score_result is not None,
            "overview_suspicious": overview_suspicious,
            "suspicious_case": suspicious_case,
        },
    }


__all__ = ["build_transaction_dashboard_event"]
