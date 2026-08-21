"""시연용 유사 완료 사건을 명령 실행 시에만 적재한다."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session, select  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.data.model.account import Account  # noqa: E402
from app.data.model.agent import AgentCase, AgentReview  # noqa: E402
from app.data.model.customer import Customer  # noqa: E402
from app.data.model.fraud_rule import (  # noqa: E402
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult  # noqa: E402
from app.data.model.transaction import Transaction  # noqa: E402
from app.domain.agent_status import AgentExecutionStatus  # noqa: E402
from app.domain.fraud_type_codes import (  # noqa: E402
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)


FRAUD_TYPES = (
    VOICE_PHISHING,
    MESSENGER_PHISHING,
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
)
CASES_PER_TYPE = 9
RISK_SCORES = (58, 62, 66, 69, 76, 91, 74, 84, 95)
RISK_GRADES = (
    "MEDIUM",
    "MEDIUM",
    "MEDIUM",
    "MEDIUM",
    "HIGH",
    "VERY_HIGH",
    "HIGH",
    "HIGH",
    "VERY_HIGH",
)
EVIDENCE_SCENARIOS = {
    VOICE_PHISHING: (
        ("severe_amount_context",),
        ("loan_escalation_context", "severe_amount_context"),
        (
            "phone_number_manipulation",
            "loan_escalation_context",
            "severe_amount_context",
        ),
        ("all_limit_actions", "loan_escalation_context", "severe_amount_context"),
        (
            "phone_number_manipulation",
            "severe_amount_context",
            "recipient_transfer_with_severe_amount",
        ),
        (
            "phone_number_manipulation",
            "loan_escalation_context",
            "severe_amount_context",
            "recipient_transfer_with_severe_amount",
        ),
        ("remote_control", "severe_amount_context"),
        (
            "phone_number_manipulation",
            "all_limit_actions",
            "loan_escalation_context",
            "severe_amount_context",
        ),
        (
            "phone_number_manipulation",
            "all_limit_actions",
            "loan_escalation_context",
            "severe_amount_context",
            "recipient_transfer_with_severe_amount",
            "remote_control",
        ),
    ),
    MESSENGER_PHISHING: (
        ("remote_control",),
        ("remote_control", "rapid_repeat", "open_banking_with_rapid_repeat"),
        ("remote_control", "strong_auth_change_with_remote_control"),
        (
            "remote_control",
            "vulnerable_mobile_recipient_transfer",
            "rapid_repeat",
        ),
        ("remote_control", "recipient_transfer_with_remote_control"),
        (
            "remote_control",
            "strong_auth_change_with_remote_control",
            "recipient_transfer_with_remote_control",
            "open_banking_with_rapid_repeat",
            "rapid_repeat",
        ),
        ("vulnerable_mobile_recipient_transfer",),
        ("rapid_repeat", "open_banking_with_rapid_repeat"),
        (
            "remote_control",
            "strong_auth_change_with_remote_control",
            "recipient_transfer_with_remote_control",
            "vulnerable_mobile_recipient_transfer",
        ),
    ),
    ACCOUNT_TAKEOVER: (
        ("remote_control",),
        ("remote_control", "device_compromise_2plus"),
        (
            "remote_control",
            "unused_terminal_with_device_compromise",
            "device_compromise_2plus",
        ),
        ("remote_control", "impossible_travel", "connection_failures"),
        (
            "remote_control",
            "impossible_travel",
            "vpn_or_roaming_with_impossible_travel",
            "connection_failures",
        ),
        (
            "unused_terminal_with_device_compromise",
            "device_compromise_2plus",
            "remote_control",
            "strong_auth_change_with_compromise",
            "impossible_travel",
        ),
        (
            "unused_terminal_with_device_compromise",
            "device_compromise_2plus",
        ),
        (
            "impossible_travel",
            "vpn_or_roaming_with_impossible_travel",
            "connection_failures",
        ),
        (
            "unused_terminal_with_device_compromise",
            "device_compromise_2plus",
            "remote_control",
            "strong_auth_change_with_compromise",
            "impossible_travel",
            "vpn_or_roaming_with_impossible_travel",
            "connection_failures",
        ),
    ),
    FRAUD_USED_ACCOUNT: (
        ("rapid_repeat",),
        ("large_deposit_with_rapid_repeat", "rapid_repeat"),
        ("rapid_repeat", "recently_resumed_with_large_deposit"),
        (
            "rapid_repeat",
            "suspension_release_only_with_context",
            "recently_resumed_with_large_deposit",
        ),
        (
            "recipient_suspended_only_with_context",
            "large_deposit_with_rapid_repeat",
            "rapid_repeat",
        ),
        (
            "both_accounts_restricted",
            "recently_resumed_with_large_deposit",
            "large_deposit_with_rapid_repeat",
            "rapid_repeat",
        ),
        ("both_accounts_restricted",),
        (
            "suspension_release_only_with_context",
            "recently_resumed_with_large_deposit",
        ),
        (
            "recipient_suspended_only_with_context",
            "large_deposit_with_rapid_repeat",
            "rapid_repeat",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class SimilarCaseSeedResult:
    """Seed 명령 실행 결과이다."""

    rule_set_id: int
    created_count: int
    skipped_count: int


def seed_agent_similar_cases(session: Session) -> SimilarCaseSeedResult:
    """4개 사기 유형별 검토 사건 9건을 만들고 중복 사건은 건너뛴다."""

    rule_set = session.exec(
        select(FraudRuleSet)
        .where(FraudRuleSet.status == FraudRuleSetStatus.ACTIVE)
        .order_by(FraudRuleSet.version.desc())
    ).first()
    if rule_set is None or rule_set.id is None:
        raise RuntimeError("활성화된 Rule Set이 없어 시연 사건을 만들 수 없다.")

    rules = list(
        session.exec(
            select(FraudRule).where(
                FraudRule.rule_set_id == rule_set.id,
                FraudRule.type_code.in_(FRAUD_TYPES),
                FraudRule.enabled.is_(True),
            )
        ).all()
    )
    rules_by_type = {rule.type_code: rule for rule in rules}
    missing_types = set(FRAUD_TYPES) - rules_by_type.keys()
    if missing_types:
        raise RuntimeError(
            "활성 Rule Set에 시연용 사기 유형이 없다: "
            + ", ".join(sorted(missing_types))
        )

    rule_ids = [rule.id for rule in rules if rule.id is not None]
    components = list(
        session.exec(
            select(FraudRuleComponent)
            .where(FraudRuleComponent.rule_id.in_(rule_ids))
            .order_by(
                FraudRuleComponent.rule_id,
                FraudRuleComponent.sort_order,
            )
        ).all()
    )
    components_by_rule: dict[int, list[str]] = {}
    for component in components:
        components_by_rule.setdefault(component.rule_id, []).append(
            component.component_key
        )

    created_count = 0
    skipped_count = 0
    for type_index, fraud_type in enumerate(FRAUD_TYPES):
        rule = rules_by_type[fraud_type]
        component_keys = components_by_rule.get(rule.id or -1, [])
        if not component_keys:
            raise RuntimeError(f"{fraud_type} Rule에 구성요소가 없다.")

        for case_index in range(CASES_PER_TYPE):
            case_id = _case_id(type_index, case_index)
            existing_case = session.get(AgentCase, case_id)
            if existing_case is not None:
                _synchronize_existing_case(
                    session,
                    agent_case=existing_case,
                    fraud_type=fraud_type,
                    case_index=case_index,
                    component_keys=component_keys,
                )
                skipped_count += 1
                continue
            _add_resolved_case(
                session,
                rule_set_id=rule_set.id,
                fraud_type=fraud_type,
                type_index=type_index,
                case_index=case_index,
                component_keys=component_keys,
            )
            created_count += 1

    session.commit()
    return SimilarCaseSeedResult(
        rule_set_id=rule_set.id,
        created_count=created_count,
        skipped_count=skipped_count,
    )


def _add_resolved_case(
    session: Session,
    *,
    rule_set_id: int,
    fraud_type: str,
    type_index: int,
    case_index: int,
    component_keys: list[str],
) -> None:
    suffix = f"{type_index + 1:02d}-{case_index + 1:02d}"
    source_account_number = f"DEMO-SOURCE-NUMBER-{suffix}"
    recipient_account_number = f"DEMO-RECIPIENT-NUMBER-{suffix}"
    case_id = _case_id(type_index, case_index)
    occurred_at = datetime(2026, 7, 1, 9, 0, tzinfo=UTC) + timedelta(
        days=type_index * CASES_PER_TYPE + case_index
    )

    customer = Customer(
        birth_date=date(1960 + case_index * 5, 1, 1),
        gender="female" if case_index % 2 == 0 else "male",
        name=f"시연고객-{suffix}",
        identification_number=f"DEMO-ID-{suffix}",
        registration_datetime=occurred_at - timedelta(days=365),
        credit_rating=min(9, 3 + case_index),
        loan_type="a",
    )
    session.add(customer)
    # 관계 객체 없이 FK 값만 지정하므로 PostgreSQL INSERT 순서를 명시한다.
    session.flush()
    customer_id = customer.id
    session.add_all(
        [
            Account(
                customer_id=customer_id,
                account_number=source_account_number,
                account_type="a",
                creation_datetime=occurred_at - timedelta(days=300),
                amount_daily_limit=50_000_000,
                current_balance=30_000_000,
                remaining_daily_limit=20_000_000,
            ),
            Account(
                account_number=recipient_account_number,
                account_type="a",
                creation_datetime=occurred_at - timedelta(days=30),
            ),
        ]
    )
    session.flush()

    amounts = (
        15_000_000,
        12_000_000,
        9_000_000,
        6_000_000,
        3_000_000,
        800_000,
        11_000_000,
        5_000_000,
        18_000_000,
    )
    channels = (
        "mobile",
        "internet",
        "atm",
        "mobile",
        "internet",
        "atm",
        "mobile",
        "internet",
        "mobile",
    )
    transaction_amount = amounts[case_index]
    transaction = Transaction(
        customer_id=customer_id,
        source_account_number=source_account_number,
        recipient_account_number=recipient_account_number,
        transaction_datetime=occurred_at,
        transaction_amount=transaction_amount,
        channel=channels[case_index],
        type_general_automatic="general",
        access_medium="a",
        error_code="a",
        num_connection_failure=case_index,
        initial_balance=30_000_000,
        balance=30_000_000 - transaction_amount,
        operating_system="Android",
        rooting_jailbreak_indicator=False,
        mobile_roaming_indicator=False,
        vpn_indicator=case_index == 2,
        flag_terminal_malicious_behavior_1=False,
        flag_terminal_malicious_behavior_2=(fraud_type == ACCOUNT_TAKEOVER),
        flag_terminal_malicious_behavior_3=False,
        flag_terminal_malicious_behavior_5=False,
        flag_terminal_malicious_behavior_6=False,
    )
    session.add(transaction)
    session.flush()
    if transaction.id is None:
        raise RuntimeError("시연용 거래 정수 ID를 생성하지 못했다.")
    transaction_id = transaction.id
    session.add(
        MLPredictionResult(
            transaction_id=transaction_id,
            predict_result=True,
            predict_proba=(
                0.96,
                0.91,
                0.86,
                0.80,
                0.74,
                0.67,
                0.82,
                0.90,
                0.98,
            )[case_index],
            model_name="demo-fraud-model",
            model_version="seed-1.0",
            latency_ms=25 + case_index,
        )
    )

    score_result = FraudTypeScoreResult(
        transaction_id=transaction_id,
        rule_set_id=rule_set_id,
        rule_filter_status="APPLIED",
        primary_fraud_type=fraud_type,
        type_scores=_type_scores(fraud_type, case_index),
        matched_components={
            fraud_type: _select_component_keys(
                component_keys,
                fraud_type,
                case_index,
            )
        },
        created_at=occurred_at,
    )
    session.add(score_result)
    session.flush()
    if score_result.id is None:
        raise RuntimeError("시연용 Rule 결과 식별자를 생성하지 못했다.")

    # 실제 거래의 위험등급이 달라도 동종 완료 사건을 찾을 수 있도록 분산한다.
    completed_at = occurred_at + timedelta(seconds=5)
    session.add(
        AgentCase(
            case_id=case_id,
            transaction_id=transaction_id,
            fraud_type_score_result_id=score_result.id,
            execution_status=AgentExecutionStatus.COMPLETED.value,
            risk_score=RISK_SCORES[case_index],
            risk_grade=RISK_GRADES[case_index],
            response_result={
                "applied_fraud_type": fraud_type,
                "information_status": "SUFFICIENT",
                "summary": f"시연용 {fraud_type} 대응 완료 사건이다.",
                "recommended_actions": [],
                "checklist": [],
            },
            generation_metadata={"seed_data": True},
            created_at=occurred_at,
            completed_at=completed_at,
        )
    )
    session.flush()
    session.add(
        AgentReview(
            case_id=case_id,
            reviewer_id="DEMO-REVIEWER",
            decision="CONFIRMED_FRAUD",
            confirmed_fraud_type=fraud_type,
            performed_actions=_performed_actions(fraud_type),
            checklist_results=[
                {
                    "item_code": "CUSTOMER_CONFIRMED",
                    "checked": True,
                }
            ],
            resolution_summary=(
                f"담당자가 {fraud_type} 유형으로 확정하고 대응을 완료했다."
            ),
            reviewed_at=completed_at,
        )
    )


def _case_id(type_index: int, case_index: int) -> str:
    return f"DEMO-CASE-{type_index + 1:02d}-{case_index + 1:02d}"


def _synchronize_existing_case(
    session: Session,
    *,
    agent_case: AgentCase,
    fraud_type: str,
    case_index: int,
    component_keys: list[str],
) -> None:
    """이전 Seed 사건의 위험도와 담당자 확정 결과를 최신 구성으로 맞춘다."""

    agent_case.risk_score = RISK_SCORES[case_index]
    agent_case.risk_grade = RISK_GRADES[case_index]
    score_result = session.get(
        FraudTypeScoreResult,
        agent_case.fraud_type_score_result_id,
    )
    if score_result is not None:
        score_result.type_scores = _type_scores(fraud_type, case_index)
        score_result.matched_components = {
            fraud_type: _select_component_keys(
                component_keys,
                fraud_type,
                case_index,
            )
        }
    review = session.get(AgentReview, agent_case.case_id)
    if review is None:
        return
    review.decision = "CONFIRMED_FRAUD"
    review.confirmed_fraud_type = fraud_type
    review.performed_actions = _performed_actions(fraud_type)
    review.resolution_summary = (
        f"담당자가 {fraud_type} 유형으로 확정하고 대응을 완료했다."
    )


def _type_scores(primary_type: str, case_index: int) -> dict[str, float]:
    primary_scores = (0.90, 0.84, 0.78, 0.72, 0.63, 0.57, 0.70, 0.80, 0.90)
    scores = {fraud_type: 0.10 for fraud_type in FRAUD_TYPES}
    scores[primary_type] = primary_scores[case_index]
    secondary_index = (FRAUD_TYPES.index(primary_type) + 1) % len(FRAUD_TYPES)
    scores[FRAUD_TYPES[secondary_index]] = (
        0.42,
        0.38,
        0.34,
        0.30,
        0.55,
        0.53,
        0.20,
        0.25,
        0.30,
    )[case_index]
    return scores


def _select_component_keys(
    keys: list[str],
    fraud_type: str,
    case_index: int,
) -> list[str]:
    scenario = EVIDENCE_SCENARIOS[fraud_type][case_index]
    missing_keys = set(scenario) - set(keys)
    if missing_keys:
        raise RuntimeError(
            f"{fraud_type} Seed 근거가 활성 Rule에 없다: "
            + ", ".join(sorted(missing_keys))
        )
    return list(scenario)


def _performed_actions(fraud_type: str) -> list[dict[str, object]]:
    action_code = {
        VOICE_PHISHING: "GUIDE_VOICE_PHISHING_RESPONSE",
        MESSENGER_PHISHING: "GUIDE_MESSENGER_PHISHING_RESPONSE",
        ACCOUNT_TAKEOVER: "GUIDE_SECURITY_CHECK",
        FRAUD_USED_ACCOUNT: "REVIEW_ACCOUNT_FLOW",
    }[fraud_type]
    return [
        {
            "action_code": action_code,
            "performed": True,
            "fraud_type": fraud_type,
        }
    ]


def main() -> None:
    with Session(engine) as session:
        result = seed_agent_similar_cases(session)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
