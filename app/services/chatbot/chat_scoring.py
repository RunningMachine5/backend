from __future__ import annotations
from collections.abc import Iterable
from app.domain.fraud_circumstance_codes import FRAUD_CIRCUMSTANCE_SCORES
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


def score_chat_fraud_circumstances(
    circumstance_codes: Iterable[str],
) -> dict[str, int]:
    """상담 종료 시 한 번 호출된다. 상담 종료 시 사기 정황을 사기유형 점수로 집계"""

    type_scores = {type_code: 0 for type_code in sorted(FINAL_FRAUD_TYPE_CODES)}

    for circumstance_code in circumstance_codes:
        scores = FRAUD_CIRCUMSTANCE_SCORES.get(circumstance_code)
        # 채점표에 없는 로직은 건너뛴다
        if scores is None:
            continue
        for type_code, score in scores.items():
            # 채점표에 있으나 운영 중이 아닌 유형이 생기면 그때 유형을 늘린다.
            if type_code not in type_scores:
                continue
            type_scores[type_code] += score

    return type_scores

__all__ = ["score_chat_fraud_circumstances"]
