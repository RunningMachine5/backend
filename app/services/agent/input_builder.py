"""사기 탐지 결과를 Agent 실행 입력으로 변환한다."""

from app.domain.agent_status import RuleFilterStatus
from app.dto.agent import AgentInputDTO
from app.pipelines.d_fraud_detection_pipline import FraudDetectionResult
from app.services.analysis.risk_grader import RiskGrader


def build_agent_input(result: FraudDetectionResult) -> AgentInputDTO | None:
    prediction = result.prediction_result
    score_result = result.score_result
    transaction_id = result.transaction.id
    if (
        prediction is None
        or not prediction.predict_result
        or score_result is None
        or score_result.rule_filter_status != RuleFilterStatus.APPLIED.value
        or transaction_id is None
        or score_result.id is None
    ):
        return None

    risk = RiskGrader().assess(
        result.transaction.transaction_amount,
        prediction.predict_proba,
    )
    return AgentInputDTO(
        transaction_id=transaction_id,
        fraud_type_score_result_id=score_result.id,
        risk_score=risk.risk_score,
        risk_grade=risk.risk_grade,
    )


__all__ = ["build_agent_input"]
