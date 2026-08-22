"""고객에게 직접 출력하는 고정 안내 문구."""

from __future__ import annotations

from datetime import datetime
from string import Template

from app.domain.fraud_type_codes import get_fraud_type_display_name


# 챗봇 최초 접속 알림
INITIAL_NOTIFICATION_TEMPLATE = Template(
    "안녕하세요 고객님의\n"
    "$transaction_datetime $transaction_amount $transaction_direction\n"
    "거래에서 전자금융사고 예방을 위한 확인 필요 사항이 발생하여"
    " 현재 일시적으로 처리 보류 중입니다\n"
)

# 판별 종료 및 자유 대화 전환 안내
FRAUD_DISCRIMINATION_FAILED_MESSAGE = (
    "분석 결과 어떤 이상거래 유형인지 정확히 판별이 힘들어요. "
    "상담사를 연결해드릴게요."
)
HANDOFF_WAITING_MESSAGE = "상담사에게 곧 전화가 올 거예요."
HANDOFF_FREE_CHAT_MESSAGE = (
    "그동안 저에게 질문을 해주시면 대답해드릴게요!"
)
FREE_CHAT_PROMPT = (
    "대응 가이드 이외에 추가로 질문하고 싶은 것이 있다면 말씀해주세요."
)
NORMAL_GUIDE_MESSAGE = (
    "확인해주신 내용으로는 현재 거래가 사기 거래일 가능성이 낮아 보여요.\n\n"
    "은행 계좌가 지급정지된 상태라면 은행 고객센터(1599-9999)에 먼저 문의해 "
    "지급정지 사유와 해제 방법을 확인해 주세요.\n\n"
    "본인계좌 일괄지급정지 서비스를 통해 정지된 계좌는 은행 영업점을 방문해야 "
    "해제할 수 있어요. 방문 전 고객센터에서 필요한 신분증이나 서류를 확인하시면 "
    "더 편리합니다."
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


def render_fraud_type_confirmed_message(fraud_type: str) -> str:
    """사기유형 확정 팝업에 표시할 문구."""

    display_name = get_fraud_type_display_name(fraud_type)
    subject_particle = "이" if _has_final_consonant(display_name) else "가"
    return (
        f"{display_name}{subject_particle} 의심돼요, "
        "대응 가이드를 알려드릴게요."
    )


def _has_final_consonant(text: str) -> bool:
    """한글 표시명의 마지막 글자에 받침이 있는지 확인한다."""

    if not text:
        return False
    codepoint = ord(text[-1])
    return 0xAC00 <= codepoint <= 0xD7A3 and (codepoint - 0xAC00) % 28 != 0

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
    "FRAUD_DISCRIMINATION_FAILED_MESSAGE",
    "FREE_CHAT_PROMPT",
    "HANDOFF_FREE_CHAT_MESSAGE",
    "HANDOFF_WAITING_MESSAGE",
    "INITIAL_NOTIFICATION_TEMPLATE",
    "NEXT_QUESTION_MESSAGE",
    "NORMAL_GUIDE_MESSAGE",
    "TOO_VAGUE_MESSAGE",
    "UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE",
    "WANT_END_MESSAGE",
    "render_fraud_type_confirmed_message",
    "render_initial_notification",
]
