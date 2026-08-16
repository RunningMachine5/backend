"""고객에게 그대로 출력하는 안내 문구.

문구의 출처는 docs/customer-chatbot/messages.md 한 곳뿐이다.
코드에서 문구를 새로 쓰지 않고, 바꿔야 하면 문서를 먼저 고친 뒤 여기로 옮긴다.
LLM이 생성하지 않고 애플리케이션 코드가 그대로 출력한다.
"""

from __future__ import annotations

from datetime import datetime
from string import Template


# B.1 최초 알림 메시지 — 챗봇 접속 직후 1회
INITIAL_NOTIFICATION_TEMPLATE = Template(
    "고객님의\n"
    "$transaction_datetime $transaction_amount $transaction_direction\n"
    "거래에서 전자금융사고 예방을 위한 확인 필요 사항이 발생하여"
    " 현재 일시적으로 처리 보류 중입니다\n"
    "금융사기가 의심되거나 관련된 자세한 상담을 받고 싶으시면\n"
    "챗봇 상담 버튼을 눌러주세요"
)

# B.2 버튼 선택 시 출력 — "챗봇 상담"은 문구 없이 바로 question_step 1 질문으로 간다.
HANDOFF_WAITING_MESSAGE = (
    "정확한 안내를 위해 상담사를 연결해드릴게요. 잠시만 기다려주세요."
)
END_CHAT_MESSAGE = (
    "상담을 종료합니다."
    " 거래 제한 해제와 관련된 자세한 안내는 고객센터로 문의해 주세요."
)

# B.3 평가 판정별 안내 — SUFFICIENT는 문구가 없고 WANT_END는 B.6을 쓴다.
TOO_VAGUE_MESSAGE = "좀 더 구체적으로 다시 말해주실 수 있을까요?"
# B.4 재시도 소진 또는 평가 LLM 장애 시 다음 질문 전환 안내
NEXT_QUESTION_MESSAGE = "알겠습니다 다음 질문을 할게요"

# B.5 안내를 만들지 못한 가이드 검색 질의 — 검색 0건이거나 Generate가 답하지 못한 경우
UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE = (
    "말씀해주신 이 부분은 제가 안내해드릴 수 있는 자료를 찾지 못했어요.\n"
    "정확한 안내가 필요하시면 상담사를 연결해드릴게요."
)

# B.6 상담 종료 요청(WANT_END) 시 상담사 연결 안내
WANT_END_HANDOFF_MESSAGE = (
    "상담을 종료하겠습니다."
    " 정확한 안내를 위해 상담사를 연결해드릴게요. 잠시만 기다려주세요."
)

# B.7 챗봇 접속 안내 이메일 — 대화창이 아니라 메일 본문으로 나가는 유일한 문구다.
CHAT_URL_EMAIL_SUBJECT = "[FDShield] 거래 확인이 필요합니다"
# 고객명을 알 수 없을 때 쓰는 값. "고객님"으로 읽힌다.
CHAT_URL_EMAIL_DEFAULT_CUSTOMER_NAME = "고객"
CHAT_URL_EMAIL_BODY_TEMPLATE = Template(
    "안녕하세요, $customer_name님.\n"
    "최근 거래에서 전자금융사고 예방을 위한 확인 필요 사항이 발생하여"
    " 해당 거래를 현재 일시적으로 처리 보류 중입니다.\n"
    "\n"
    "거래 일시: $transaction_datetime\n"
    "거래 금액: $transaction_amount $transaction_direction\n"
    "\n"
    "아래 주소에서 거래 확인과 금융사기 대응 안내를 받으실 수 있습니다.\n"
    "$chat_url\n"
    "\n"
    "접속 후 본인 확인을 위해 출생연도 4자리를 입력해 주세요.\n"
    "\n"
    "본인이 하지 않은 거래라면 금융회사 공식 고객센터로 즉시 신고해 주세요.\n"
    "FDShield는 이메일로 비밀번호나 인증번호를 요구하지 않습니다.\n"
)


def render_initial_notification(
    *,
    transaction_datetime: datetime,
    transaction_amount: int,
) -> str:
    """B.1의 거래시각·거래금액·입금/출금을 거래 원장 값으로 치환한다.

    입금/출금은 PRD 2.3대로 금액의 부호로 판정한다(음수=출금, 양수=입금).
    치환값의 표기 형식은 설계 문서가 정하지 않아 여기서 고정한다.
    """

    return INITIAL_NOTIFICATION_TEMPLATE.substitute(
        transaction_datetime=_format_datetime(transaction_datetime),
        transaction_amount=_format_amount(transaction_amount),
        transaction_direction=_format_direction(transaction_amount),
    )


def render_chat_url_email_body(
    *,
    customer_name: str | None,
    transaction_datetime: datetime,
    transaction_amount: int,
    chat_url: str,
) -> str:
    """B.7 이메일 본문을 거래 원장 값과 세션 접속 URL로 채운다.

    거래시각·거래금액·입금/출금 표기는 B.1과 같다.
    """

    name = (customer_name or "").strip() or CHAT_URL_EMAIL_DEFAULT_CUSTOMER_NAME
    return CHAT_URL_EMAIL_BODY_TEMPLATE.substitute(
        customer_name=name,
        transaction_datetime=_format_datetime(transaction_datetime),
        transaction_amount=_format_amount(transaction_amount),
        transaction_direction=_format_direction(transaction_amount),
        chat_url=chat_url,
    )


def _format_datetime(transaction_datetime: datetime) -> str:
    return transaction_datetime.strftime("%Y-%m-%d %H:%M")


def _format_amount(transaction_amount: int) -> str:
    return f"{abs(transaction_amount):,}원"


def _format_direction(transaction_amount: int) -> str:
    """음수=출금, 양수=입금 (PRD 2.3)."""

    return "출금" if transaction_amount < 0 else "입금"


__all__ = [
    "CHAT_URL_EMAIL_BODY_TEMPLATE",
    "CHAT_URL_EMAIL_DEFAULT_CUSTOMER_NAME",
    "CHAT_URL_EMAIL_SUBJECT",
    "END_CHAT_MESSAGE",
    "HANDOFF_WAITING_MESSAGE",
    "INITIAL_NOTIFICATION_TEMPLATE",
    "NEXT_QUESTION_MESSAGE",
    "TOO_VAGUE_MESSAGE",
    "UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE",
    "WANT_END_HANDOFF_MESSAGE",
    "render_chat_url_email_body",
    "render_initial_notification",
]
