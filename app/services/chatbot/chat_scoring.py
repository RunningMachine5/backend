from __future__ import annotations
from collections.abc import Iterable
from app.data.model.chatbot import ChatSession
from app.domain.fraud_circumstance_codes import FRAUD_CIRCUMSTANCE_SCORES
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.repositories.chat_session import ChatSessionRepository


def score_chat_fraud_circumstances(
    circumstance_codes: Iterable[str],
) -> dict[str, int]:
    """사기 정황이 추출될 때마다 세션의 정황 전체를 다시 읽어 사기유형 점수로 집계한다."""

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


def rescore_chat_session(
    repository: ChatSessionRepository,
    chat_session: ChatSession,
) -> dict[str, int]:
    """세션에 쌓인 사기 정황 전체를 다시 읽어 유형별 점수를 갱신한다(PRD 2.6).

    증분 가산이 아니라 매번 전체 재계산이므로, 같은 정황이 여러 턴에 걸쳐 다시
    추출돼도 이중 가산되지 않는다. 세션당 정황은 최대 20종
    (``FINAL_FRAUD_CIRCUMSTANCE_CODES``)으로 상한이 있어 조회·합산·upsert 모두
    인메모리 수준의 비용이다.

    커밋은 하지 않는다 — 부르는 쪽(백그라운드 추출 작업, 파이프라인 턴)이 소유한다.
    """

    type_scores = score_chat_fraud_circumstances(
        circumstance.circumstance_code
        for circumstance in repository.list_fraud_circumstances(chat_session)
    )
    repository.upsert_fraud_type_scores(chat_session, type_scores=type_scores)
    return type_scores


__all__ = ["rescore_chat_session", "score_chat_fraud_circumstances"]
