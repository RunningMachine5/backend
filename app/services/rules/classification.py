"""이진 ML 결과 뒤에 사기유형 룰 분류 결과를 만든다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlmodel import Session

from app.data.model.fraud_rule import (
    FraudTypeClassificationResult,
    FraudTypeClassificationStatus,
)
from app.services.rules.engine import RuleEngine, RuleSetValidationError
from app.services.rules.expression_evaluator import RuleExpressionError
from app.services.rules.feature_builder import RuleFeatureError
from app.services.rules.repository import get_active_rule_set


def classify_transaction_fraud_type(
    *,
    session: Session,
    transaction_id: str,
    raw_data: Mapping[str, Any],
    is_fraud: bool,
    engine: RuleEngine | None = None,
) -> FraudTypeClassificationResult:
    """거래의 룰 분류 결과를 생성한다.

    정상 거래는 유형 분류 대상이 아니므로 SKIPPED로 남긴다. 룰 설정 오류는
    이진 ML 결과 저장을 방해하지 않도록 FAILED 결과로 격리한다.
    """

    if not is_fraud:
        return FraudTypeClassificationResult(
            transaction_id=transaction_id,
            status=FraudTypeClassificationStatus.SKIPPED,
        )

    active = get_active_rule_set(session)
    if active is None:
        return FraudTypeClassificationResult(
            transaction_id=transaction_id,
            status=FraudTypeClassificationStatus.FAILED,
            error_message="활성 룰셋이 없어 사기유형을 분류하지 못했습니다.",
        )

    persisted_rule_set, definition = active
    try:
        classified = (engine or RuleEngine()).classify(raw_data, definition)
    except (RuleSetValidationError, RuleExpressionError, RuleFeatureError) as exc:
        return FraudTypeClassificationResult(
            transaction_id=transaction_id,
            status=FraudTypeClassificationStatus.FAILED,
            rule_set_version=persisted_rule_set.version,
            error_message=str(exc)[:1000],
        )

    return FraudTypeClassificationResult(
        transaction_id=transaction_id,
        status=FraudTypeClassificationStatus(classified.status),
        fraud_type=classified.fraud_type,
        top_score=classified.top_score,
        second_score=classified.second_score,
        score_gap=classified.score_gap,
        type_scores=classified.type_scores,
        matched_components=classified.matched_components,
        rule_set_version=persisted_rule_set.version,
    )


__all__ = ["classify_transaction_fraud_type"]
