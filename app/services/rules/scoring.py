"""ML이 사기로 판정한 거래의 유형별 룰 점수를 만든다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session

from app.data.model.fraud_rule import FraudTypeScoreResult
from app.services.rules.engine import RuleEngine, RuleSetValidationError
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.feature_builder import RuleFeatureError
from app.services.rules.repository import get_active_rule_set


logger = logging.getLogger(__name__)


def score_transaction_fraud_types(
    *,
    session: Session,
    transaction_id: str,
    raw_data: Mapping[str, Any],
    engine: RuleEngine | None = None,
) -> FraudTypeScoreResult | None:
    """점수를 생성하되 룰 오류가 ML 결과 저장을 막지는 않게 한다."""

    active = get_active_rule_set(session)
    if active is None:
        logger.warning(
            "활성 룰셋이 없어 거래 %s의 유형별 점수를 계산하지 못했습니다.",
            transaction_id,
        )
        return None

    persisted_rule_set, definition = active
    try:
        scored = (engine or RuleEngine()).score(raw_data, definition)
    except (RuleSetValidationError, RuleExpressionError, RuleFeatureError):
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
