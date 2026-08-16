"""question_step과 top_fraud_types로 결정되는 챗봇 질문 문구.

문구의 출처는 docs/customer-chatbot/README.md 의 2.4 정보 수집 — 챗봇 질문이다.
질문은 분기 조건과 붙어 있어야 읽히므로 messages.md 가 아니라 PRD 본문에 있고,
여기로 옮길 때도 문구를 새로 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)


# question_step 1의 시작 멘트. 유형판별 질문 앞에 붙는다.
GREETING = (
    "안녕하세요 FDShield의 챗봇 이지스입니다\n"
    "고객님의 상황을 판단하기 위해 먼저 몇 가지 간단한 질문을 드릴게요!"
)

# question_step 2 이상에서 상한 없이 반복하는 추가 질문 멘트.
FOLLOW_UP_QUESTION = (
    "지금까지 말씀해주신 것 외에, 그 상황에서 따로 하신 행동"
    "(예: 링크 클릭, 앱 설치, 송금, 정보 입력 등)이 있으신가요?\n"
    '없으시면 "종료할게요"라고 말씀해주세요.'
)

# top_fraud_types 가 없는 세션(룰 채점 실패)이 유형판별 질문 대신 쓰는 일반 질문.
GENERAL_FALLBACK_QUESTION = (
    "이 거래를 알고 계셨는지, 본인이 직접 실행하거나 승인한 거래인지 말씀해 주세요."
)

# 상위 2개 후보 유형의 조합(순서 무관)으로 고르는 유형판별 질문 6종.
TYPE_DISCRIMINATION_QUESTIONS: Mapping[frozenset[str], str] = {
    frozenset({VOICE_PHISHING, MESSENGER_PHISHING}): (
        "이번 거래나 정보 제공을 하게 만든 상대와 주로 어떻게 연락했나요:"
        " 전화로 연락한 수사기관·금융회사·대출상담사 등이었나요,"
        " 아니면 카카오톡·문자·SNS로 연락한 가족·지인이었나요?"
        " 실제 연락 방식과 상대가 누구라고 했는지 말씀해주세요."
    ),
    frozenset({VOICE_PHISHING, ACCOUNT_TAKEOVER}): (
        "문제 거래는 통화 상대의 지시를 받고 고객님이 직접 송금·승인하거나"
        " 현금을 전달한 것인가요, 아니면 고객님은 거래를 입력하거나 승인하지 않았는데"
        " 계정에서 본인 모르게 발생한 것인가요?"
    ),
    frozenset({VOICE_PHISHING, FRAUD_USED_ACCOUNT}): (
        "문제 자금은 고객님의 예금이나 대출금을 상대에게 송금·전달한 것인가요,"
        " 아니면 다른 사람에게서 고객님 계좌로 들어온 돈을 다시"
        " 송금·출금·전달한 것인가요?"
    ),
    frozenset({MESSENGER_PHISHING, ACCOUNT_TAKEOVER}): (
        "가족·지인이라고 믿은 메신저 상대의 요청을 보고 고객님이 직접 송금하거나"
        " 정보를 제공한 것인가요, 아니면 고객님의 메신저·쇼핑·금융 계정에서"
        " 본인이 하지 않은 메시지·결제·거래가 발생한 것인가요?"
    ),
    frozenset({MESSENGER_PHISHING, FRAUD_USED_ACCOUNT}): (
        "메신저로 연락한 가족·지인의 요청을 믿고 고객님의 돈을 보낸 것인가요,"
        " 아니면 타인에게서 고객님 계좌로 돈을 받은 뒤 메신저 지시에 따라 다시"
        " 송금·출금·전달한 것인가요?"
    ),
    frozenset({ACCOUNT_TAKEOVER, FRAUD_USED_ACCOUNT}): (
        "계좌나 인증정보가 속아서 탈취되어 고객님 모르게 거래가 발생한 것인가요,"
        " 아니면 다른 사람이 계좌를 사용하도록 빌려주거나 고객님이 입금된 돈을"
        " 직접 재송금·출금·전달한 것인가요?"
    ),
}


def select_type_discrimination_question(
    top_fraud_types: Sequence[str] | None,
) -> str:
    """상위 2개 후보 유형 조합에 맞는 유형판별 질문을 고른다.

    조합이 없거나(룰 채점 실패) 등록되지 않은 조합이면 일반 질문으로 폴백한다.
    """

    if top_fraud_types is None or len(top_fraud_types) != 2:
        return GENERAL_FALLBACK_QUESTION
    return TYPE_DISCRIMINATION_QUESTIONS.get(
        frozenset(top_fraud_types),
        GENERAL_FALLBACK_QUESTION,
    )


def render_question(
    *,
    question_step: int,
    top_fraud_types: Sequence[str] | None,
) -> str:
    """해당 질문 단계에서 고객에게 출력할 질문 전문을 만든다."""

    if question_step <= 1:
        return f"{GREETING}\n\n{select_type_discrimination_question(top_fraud_types)}"
    return FOLLOW_UP_QUESTION


__all__ = [
    "FOLLOW_UP_QUESTION",
    "GENERAL_FALLBACK_QUESTION",
    "GREETING",
    "TYPE_DISCRIMINATION_QUESTIONS",
    "render_question",
    "select_type_discrimination_question",
]
