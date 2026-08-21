"""고객에게 직접 출력하는 고정 안내 문구."""

from __future__ import annotations

from datetime import datetime
from string import Template


# 챗봇 최초 접속 알림
INITIAL_NOTIFICATION_TEMPLATE = Template(
    "고객님의\n"
    "$transaction_datetime $transaction_amount $transaction_direction\n"
    "거래에서 전자금융사고 예방을 위한 확인 필요 사항이 발생하여"
    " 현재 일시적으로 처리 보류 중입니다\n"
    "금융사기가 의심되거나 관련된 자세한 상담을 받고 싶으시면\n"
    "챗봇 상담 버튼을 눌러주세요"
)

# 버튼 선택 결과
HANDOFF_WAITING_MESSAGE = (
    "정확한 안내를 위해 상담사를 연결해드릴게요. 잠시만 기다려주세요."
)
END_CHAT_MESSAGE = (
    "상담을 종료합니다."
    " 거래 제한 해제와 관련된 자세한 안내는 고객센터로 문의해 주세요."
)

# 답변 분석 결과 안내
TOO_VAGUE_MESSAGE = "저는 금융사기와 관련된 질문에만 대답이 가능해요 관련된 내용을 좀 더 구체적으로 말씀해주실 수 있을까요?"
NEXT_QUESTION_MESSAGE = "알겠습니다 다음 질문을 할게요"

# 가이드 검색 또는 생성 실패 안내
UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE = (
    "말씀해주신 이 부분은 제가 안내해드릴 수 있는 자료를 찾지 못했어요.\n"
    "정확한 안내가 필요하시면 상담사를 연결해드릴게요."
)

WANT_END_MESSAGE = "상담을 종료하겠습니다."

def render_initial_notification(
    *,
    transaction_datetime: datetime,
    transaction_amount: int,
) -> str:
    """거래 원장 값으로 최초 알림을 렌더링한다."""

    return INITIAL_NOTIFICATION_TEMPLATE.substitute(
        transaction_datetime=_format_datetime(transaction_datetime),
        transaction_amount=_format_amount(transaction_amount),
        transaction_direction=_format_direction(transaction_amount),
    )


def _format_datetime(transaction_datetime: datetime) -> str:
    return transaction_datetime.strftime("%Y-%m-%d %H:%M")


def _format_amount(transaction_amount: int) -> str:
    return f"{abs(transaction_amount):,}원"


def _format_direction(transaction_amount: int) -> str:
    """금액 부호로 입금과 출금을 구분한다."""

    return "출금" if transaction_amount < 0 else "입금"


__all__ = [
    "END_CHAT_MESSAGE",
    "HANDOFF_WAITING_MESSAGE",
    "INITIAL_NOTIFICATION_TEMPLATE",
    "NEXT_QUESTION_MESSAGE",
    "TOO_VAGUE_MESSAGE",
    "UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE",
    "WANT_END_MESSAGE",
    "render_initial_notification",
]
