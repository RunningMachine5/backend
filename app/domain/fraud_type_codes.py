"""현재 운영하는 사기유형 코드와 화면 표시 이름을 관리한다."""

from __future__ import annotations

from collections.abc import Mapping


VOICE_PHISHING = "VOICE_PHISHING"
MESSENGER_PHISHING = "MESSENGER_PHISHING"
ACCOUNT_TAKEOVER = "ACCOUNT_TAKEOVER"
FRAUD_USED_ACCOUNT = "FRAUD_USED_ACCOUNT"


# Rule Engine의 type_code와 동일한 영문 코드를 사용하고 한글 문구는 별도로 관리한다.
FRAUD_TYPE_DISPLAY_NAMES: Mapping[str, str] = {
    VOICE_PHISHING: "보이스피싱",
    MESSENGER_PHISHING: "메신저피싱",
    ACCOUNT_TAKEOVER: "계정탈취",
    FRAUD_USED_ACCOUNT: "사기이용계좌",
}

FINAL_FRAUD_TYPE_CODES = frozenset(FRAUD_TYPE_DISPLAY_NAMES)


def get_fraud_type_display_name(type_code: str) -> str:
    """등록되지 않은 신규 유형은 코드 자체를 표시해 확장성을 유지한다."""

    return FRAUD_TYPE_DISPLAY_NAMES.get(type_code, type_code)


__all__ = [
    "ACCOUNT_TAKEOVER",
    "FINAL_FRAUD_TYPE_CODES",
    "FRAUD_TYPE_DISPLAY_NAMES",
    "FRAUD_USED_ACCOUNT",
    "MESSENGER_PHISHING",
    "VOICE_PHISHING",
    "get_fraud_type_display_name",
]
