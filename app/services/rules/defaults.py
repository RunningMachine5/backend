"""최종 확정된 이상거래 사기유형 2차 분류 기본 룰셋."""

from __future__ import annotations

from typing import Any

from app.services.rules.engine import (
    FraudRuleDefinition,
    RuleComponentDefinition,
    RuleSetDefinition,
)


def _condition(field: str, operator: str, value: Any) -> dict[str, Any]:
    return {"field": field, "operator": operator, "value": value}


def _and(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {"operator": "AND", "conditions": list(conditions)}


def _card_context(condition: dict[str, Any]) -> dict[str, Any]:
    """카드 대용 맥락이 아니면 카드부정사용 점수가 항상 0이 되게 한다."""

    return _and(
        _condition("card_context_proxy", "EQ", True),
        condition,
    )


DEFAULT_RULE_SET = RuleSetDefinition(
    version="v1",
    rules=(
        FraudRuleDefinition(
            type_code="VOICE_PHISHING",
            display_name="보이스피싱",
            components=(
                RuleComponentDefinition(
                    component_key="phone_number_manipulation",
                    name="전화번호 조작",
                    condition_expression=_condition(
                        "phone_number_manipulation",
                        "EQ",
                        True,
                    ),
                    weight=0.25,
                ),
                RuleComponentDefinition(
                    component_key="loan_related",
                    name="대출 관련",
                    condition_expression=_condition("loan_related", "EQ", True),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="limit_adjustment_detected",
                    name="한도 문의·증액·해제",
                    condition_expression=_condition(
                        "limit_adjustment_detected",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="high_value_or_balance_pressure",
                    name="금액 이상·잔액 소진·일 한도 근접",
                    condition_expression=_condition(
                        "high_value_or_balance_pressure",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="new_or_rare_recipient",
                    name="신규·희소 수취인",
                    condition_expression=_condition(
                        "new_or_rare_recipient",
                        "EQ",
                        True,
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="another_person_account",
                    name="타계좌 이체",
                    condition_expression=_condition(
                        "Another_Person_Account",
                        "EQ",
                        1,
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="remote_control",
                    name="원격제어",
                    condition_expression=_condition("remote_control", "EQ", True),
                    weight=0.05,
                ),
            ),
        ),
        FraudRuleDefinition(
            type_code="MESSENGER_PHISHING",
            display_name="메신저피싱",
            components=(
                RuleComponentDefinition(
                    component_key="remote_control",
                    name="원격제어",
                    condition_expression=_condition("remote_control", "EQ", True),
                    weight=0.25,
                ),
                RuleComponentDefinition(
                    component_key="open_banking_used",
                    name="오픈뱅킹 사용",
                    condition_expression=_condition(
                        "Account_indicator_Openbanking",
                        "EQ",
                        1,
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="authentication_changed",
                    name="최근 인증정보 변경",
                    condition_expression=_condition(
                        "authentication_changed",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="vulnerable_mobile_environment",
                    name="고령자 모바일·취약 iOS 환경",
                    condition_expression=_condition(
                        "vulnerable_mobile_environment",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="new_recipient_transfer",
                    name="신규·희소 수취인 타계좌 이체",
                    condition_expression=_condition(
                        "new_recipient_transfer",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="rapid_repeat",
                    name="단시간 반복 이체",
                    condition_expression=_condition("rapid_repeat", "EQ", True),
                    weight=0.10,
                ),
            ),
        ),
        FraudRuleDefinition(
            type_code="ACCOUNT_TAKEOVER",
            display_name="계정탈취",
            components=(
                RuleComponentDefinition(
                    component_key="unused_terminal",
                    name="미사용 단말",
                    condition_expression=_condition(
                        "Unused_terminal_status",
                        "EQ",
                        1,
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="device_compromise_count",
                    name="단말침해 신호 2개 이상",
                    condition_expression=_condition(
                        "device_compromise_count",
                        "GTE",
                        2,
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="remote_control",
                    name="원격제어",
                    condition_expression=_condition("remote_control", "EQ", True),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="authentication_changed",
                    name="최근 인증정보 변경",
                    condition_expression=_condition(
                        "authentication_changed",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="impossible_travel",
                    name="불가능 이동",
                    condition_expression=_condition(
                        "impossible_travel",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="vpn_or_roaming",
                    name="VPN 또는 로밍",
                    condition_expression=_condition(
                        "vpn_or_roaming",
                        "EQ",
                        True,
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="connection_failures",
                    name="접속 실패 3회 이상",
                    condition_expression=_condition(
                        "Transaction_num_connection_failure",
                        "GTE",
                        3,
                    ),
                    weight=0.05,
                ),
            ),
        ),
        FraudRuleDefinition(
            type_code="FRAUD_USED_ACCOUNT",
            display_name="사기이용계좌",
            components=(
                RuleComponentDefinition(
                    component_key="both_accounts_restricted",
                    name="정지해제·거래중지 동시 충족",
                    condition_expression=_and(
                        _condition("account_suspension_released", "EQ", True),
                        _condition("recipient_account_suspended", "EQ", True),
                    ),
                    weight=0.35,
                ),
                RuleComponentDefinition(
                    component_key="account_suspension_released",
                    name="본인계좌 최근 정지해제",
                    condition_expression=_condition(
                        "account_suspension_released",
                        "EQ",
                        True,
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="recipient_account_suspended",
                    name="수취계좌 거래중지",
                    condition_expression=_condition(
                        "recipient_account_suspended",
                        "EQ",
                        True,
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="recently_resumed",
                    name="휴면계좌의 최근 거래 재개",
                    condition_expression=_condition(
                        "recently_resumed",
                        "EQ",
                        True,
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="large_recent_deposit",
                    name="최근 7일 1천만원 이상 입금",
                    condition_expression=_condition(
                        "Flag_deposit_more_than_tenMillion",
                        "EQ",
                        1,
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="rapid_repeat",
                    name="단시간 반복 이체",
                    condition_expression=_condition("rapid_repeat", "EQ", True),
                    weight=0.05,
                ),
            ),
        ),
        FraudRuleDefinition(
            type_code="CARD_FRAUD",
            display_name="카드부정사용",
            components=(
                RuleComponentDefinition(
                    component_key="card_context_proxy",
                    name="카드거래 맥락 대용 지표",
                    condition_expression=_condition(
                        "card_context_proxy",
                        "EQ",
                        True,
                    ),
                    weight=0.25,
                ),
                RuleComponentDefinition(
                    component_key="unused_terminal",
                    name="미사용 단말",
                    condition_expression=_card_context(
                        _condition("Unused_terminal_status", "EQ", 1)
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="impossible_travel",
                    name="불가능 이동",
                    condition_expression=_card_context(
                        _condition("impossible_travel", "EQ", True)
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="rapid_repeat",
                    name="단시간 반복 거래",
                    condition_expression=_card_context(
                        _condition("rapid_repeat", "EQ", True)
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="new_or_rare_recipient",
                    name="신규·희소 수취인",
                    condition_expression=_card_context(
                        _condition("new_or_rare_recipient", "EQ", True)
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="general_transaction",
                    name="일반거래",
                    condition_expression=_card_context(
                        _condition("Type_General_Automatic", "EQ", "general")
                    ),
                    weight=0.05,
                ),
                RuleComponentDefinition(
                    component_key="vpn_or_roaming",
                    name="VPN 또는 로밍",
                    condition_expression=_card_context(
                        _condition("vpn_or_roaming", "EQ", True)
                    ),
                    weight=0.05,
                ),
            ),
        ),
    ),
)

DEFAULT_RULE_DEFINITIONS = DEFAULT_RULE_SET.rules

__all__ = ["DEFAULT_RULE_DEFINITIONS", "DEFAULT_RULE_SET"]
