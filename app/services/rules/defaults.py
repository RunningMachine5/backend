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


def _or(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {"operator": "OR", "conditions": list(conditions)}


DEFAULT_RULE_SET = RuleSetDefinition(
    version="2026-08-08-final",
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
                    weight=0.30,
                ),
                RuleComponentDefinition(
                    component_key="loan_escalation_context",
                    name="대출 상승 맥락",
                    condition_expression=_condition(
                        "loan_escalation_context",
                        "EQ",
                        True,
                    ),
                    weight=0.25,
                ),
                RuleComponentDefinition(
                    component_key="all_limit_actions",
                    name="한도 문의·증액·해제 3종 모두",
                    condition_expression=_condition(
                        "all_limit_actions",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="severe_amount_context",
                    name="이상금액과 잔액·한도 압박 동시 충족",
                    condition_expression=_condition(
                        "severe_amount_context",
                        "EQ",
                        True,
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="recipient_transfer_with_severe_amount",
                    name="신규 수취인 타계좌 이체와 심각 금액 맥락",
                    condition_expression=_and(
                        _condition("recipient_transfer", "EQ", True),
                        _condition("severe_amount_context", "EQ", True),
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
                    weight=0.30,
                ),
                RuleComponentDefinition(
                    component_key="open_banking_with_rapid_repeat",
                    name="오픈뱅킹과 반복이체 동시 충족",
                    condition_expression=_and(
                        _condition(
                            "Account_indicator_Openbanking",
                            "EQ",
                            1,
                        ),
                        _condition("rapid_repeat", "EQ", True),
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="strong_auth_change_with_remote_control",
                    name="강한 인증변경과 원격제어 동시 충족",
                    condition_expression=_and(
                        _condition("strong_auth_change", "EQ", True),
                        _condition("remote_control", "EQ", True),
                    ),
                    weight=0.20,
                ),
                RuleComponentDefinition(
                    component_key="vulnerable_mobile_recipient_transfer",
                    name="취약 모바일과 신규 수취인 타계좌 이체",
                    condition_expression=_and(
                        _condition("vulnerable_mobile", "EQ", True),
                        _condition("recipient_transfer", "EQ", True),
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="recipient_transfer_with_remote_control",
                    name="신규 수취인 타계좌 이체와 원격제어",
                    condition_expression=_and(
                        _condition("recipient_transfer", "EQ", True),
                        _condition("remote_control", "EQ", True),
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
            type_code="ACCOUNT_TAKEOVER",
            display_name="계정탈취",
            components=(
                RuleComponentDefinition(
                    component_key="unused_terminal_with_device_compromise",
                    name="미사용 단말과 단말침해 2개 이상",
                    condition_expression=_and(
                        _condition("Unused_terminal_status", "EQ", 1),
                        _condition("device_compromise_2plus", "EQ", True),
                    ),
                    weight=0.25,
                ),
                RuleComponentDefinition(
                    component_key="device_compromise_2plus",
                    name="단말침해 신호 2개 이상",
                    condition_expression=_condition(
                        "device_compromise_2plus",
                        "EQ",
                        True,
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
                    component_key="strong_auth_change_with_compromise",
                    name="강한 인증변경과 단말침해·원격제어",
                    condition_expression=_and(
                        _condition("strong_auth_change", "EQ", True),
                        _or(
                            _condition(
                                "device_compromise_2plus",
                                "EQ",
                                True,
                            ),
                            _condition("remote_control", "EQ", True),
                        ),
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
                    component_key="vpn_or_roaming_with_impossible_travel",
                    name="VPN·로밍과 불가능 이동 동시 충족",
                    condition_expression=_and(
                        _condition("vpn_or_roaming", "EQ", True),
                        _condition("impossible_travel", "EQ", True),
                    ),
                    weight=0.05,
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
                    condition_expression=_condition(
                        "suspension_pair",
                        "EQ",
                        True,
                    ),
                    weight=0.45,
                ),
                RuleComponentDefinition(
                    component_key="suspension_release_only_with_context",
                    name="정지해제만 충족하고 최근재개·고액입금",
                    condition_expression=_and(
                        _condition("suspension_release_only", "EQ", True),
                        _or(
                            _condition("recently_resumed", "EQ", True),
                            _condition(
                                "Flag_deposit_more_than_tenMillion",
                                "EQ",
                                1,
                            ),
                        ),
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="recipient_suspended_only_with_context",
                    name="수취정지만 충족하고 최근재개·고액입금",
                    condition_expression=_and(
                        _condition("recipient_suspended_only", "EQ", True),
                        _or(
                            _condition("recently_resumed", "EQ", True),
                            _condition(
                                "Flag_deposit_more_than_tenMillion",
                                "EQ",
                                1,
                            ),
                        ),
                    ),
                    weight=0.15,
                ),
                RuleComponentDefinition(
                    component_key="recently_resumed_with_large_deposit",
                    name="최근재개와 고액입금 동시 충족",
                    condition_expression=_and(
                        _condition("recently_resumed", "EQ", True),
                        _condition(
                            "Flag_deposit_more_than_tenMillion",
                            "EQ",
                            1,
                        ),
                    ),
                    weight=0.10,
                ),
                RuleComponentDefinition(
                    component_key="large_deposit_with_rapid_repeat",
                    name="고액입금과 반복이체 동시 충족",
                    condition_expression=_and(
                        _condition(
                            "Flag_deposit_more_than_tenMillion",
                            "EQ",
                            1,
                        ),
                        _condition("rapid_repeat", "EQ", True),
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
    ),
)

DEFAULT_RULE_DEFINITIONS = DEFAULT_RULE_SET.rules

__all__ = ["DEFAULT_RULE_DEFINITIONS", "DEFAULT_RULE_SET"]
