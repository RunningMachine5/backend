"""네/아니요 유형 판별 질문과 확정 유형별 고정 RAG 질의."""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.dto.chatbot import ExtractedGuideSearchQuery


OWNERSHIP_QUESTION = "본인이 한 거래가 맞나요?"

FRAUD_TYPE_CONFIRMATION_QUESTIONS: Mapping[str, str] = {
    ACCOUNT_TAKEOVER: "최근 인터넷 사이트에 개인정보 등을 입력한 적이 있나요?",
    FRAUD_USED_ACCOUNT: (
        "최근 인터넷 사이트에 계좌 비밀번호 등을 입력한 적이 있나요?"
    ),
    MESSENGER_PHISHING: (
        "최근 카카오톡이나 메시지를 통해 받은 URL에 접속한 적이 있나요?"
    ),
    VOICE_PHISHING: "최근 모르는 사람에게 금융 관련 전화가 온 적이 있나요?",
}

_PREDEFINED_GUIDE_TITLES: Mapping[str, str] = {
    ACCOUNT_TAKEOVER: "계정 탈취 의심 즉시 대응",
    FRAUD_USED_ACCOUNT: "사기이용계좌 우려 즉시 대응",
    MESSENGER_PHISHING: "메신저피싱 의심 즉시 대응",
    VOICE_PHISHING: "보이스피싱 의심 즉시 대응",
}

_PREDEFINED_GUIDE_QUERIES: Mapping[str, str] = {
    ACCOUNT_TAKEOVER: (
        "웹사이트에 개인정보를 입력한 뒤 계정 탈취가 의심됩니다. "
        "계정 비밀번호 변경, 로그인 세션 해제, 명의도용·금융피해 예방을 위한 "
        "즉시 대응 방법을 알려주세요."
    ),
    FRAUD_USED_ACCOUNT: (
        "인터넷 사이트에 계좌 비밀번호 등 계좌정보를 입력해 제 계좌가 "
        "금융사기에 악용될 우려가 있습니다. 계좌 지급정지, 비밀번호 변경, "
        "은행 신고 등 즉시 해야 할 대응 방법을 알려주세요."
    ),
    MESSENGER_PHISHING: (
        "카카오톡 또는 문자 메시지로 받은 의심스러운 URL에 접속해 메신저 피싱 "
        "피해가 우려됩니다. 악성앱 점검·삭제, 개인정보 및 금융정보 보호, "
        "신고 절차를 포함한 즉시 대응 방법을 알려주세요."
    ),
    VOICE_PHISHING: (
        "모르는 사람의 금융 관련 전화로 보이스피싱 피해가 의심됩니다. "
        "송금·개인정보 제공 여부와 관계없이 계좌 보호, 지급정지, 신고를 위해 "
        "즉시 해야 할 대응 방법을 알려주세요."
    ),
}


def render_fraud_type_confirmation_question(fraud_type: str) -> str:
    """1·2순위 유형에 맞는 네/아니요 확인 질문을 반환한다."""

    try:
        return FRAUD_TYPE_CONFIRMATION_QUESTIONS[fraud_type]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 사기유형입니다: {fraud_type}") from error


def predefined_guide_search_query(fraud_type: str) -> ExtractedGuideSearchQuery:
    """AnswerAnalyzer 없이 GuideResponder에 전달할 유형별 고정 질의."""

    try:
        search_query = _PREDEFINED_GUIDE_QUERIES[fraud_type]
        title = _PREDEFINED_GUIDE_TITLES[fraud_type]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 사기유형입니다: {fraud_type}") from error
    # 시스템 정제 질의는 고객 발언 기반 chat_guide_search_queries에 저장하지 않는다.
    return ExtractedGuideSearchQuery(
        title=title,
        search_query=search_query,
        evidence="유형 판별 퀵리플라이 결과",
    )


__all__ = [
    "FRAUD_TYPE_CONFIRMATION_QUESTIONS",
    "OWNERSHIP_QUESTION",
    "predefined_guide_search_query",
    "render_fraud_type_confirmation_question",
]
