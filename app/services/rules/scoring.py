"""ML이 사기로 판정한 거래의 유형별 룰 점수를 만든다."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.dto.ml_features import MLTransactionFeatures
from app.services.rules.engine import RuleEngine
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.repository import get_active_rule_set

logger = logging.getLogger(__name__)


def score_transaction_fraud_types(
    *,
    session: Session,
    transaction_id: int,
    features: MLTransactionFeatures,
    engine: RuleEngine | None = None,
) -> FraudTypeScoreResult | None:
    """ACTIVE 룰셋으로 유형 점수를 만들되 ML 결과 저장은 막지 않는다.

    이 함수는 Pipeline이 ML 사기 판정을 확인한 뒤에만 호출한다. ACTIVE 룰이
    없거나 룰 정의가 잘못돼도 이미 완료된 ML 예측은 유효하므로 ``None``을
    반환하고 로그만 남긴다.
    """

    active = get_active_rule_set(session)
    if active is None:
        logger.warning(
            "활성 룰셋이 없어 거래 %s의 유형별 점수를 계산하지 못했습니다.",
            transaction_id,
        )
        return None

    persisted_rule_set, definition = active
    try:
        scored = (engine or RuleEngine()).score_validated(features, definition)
    except RuleExpressionError:
        logger.exception(
            "거래 %s의 유형별 룰 점수 계산에 실패했습니다.",
            transaction_id,
        )
        return None

    return FraudTypeScoreResult(
        transaction_id=transaction_id,
        rule_set_id=persisted_rule_set.id,
        rule_filter_status="APPLIED",
        type_scores=scored.type_scores,
        matched_components=scored.matched_components,
    )


__all__ = ["score_transaction_fraud_types"]
