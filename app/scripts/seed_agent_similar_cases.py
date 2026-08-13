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
CASES_PER_TYPE = 4


@dataclass(frozen=True, slots=True)
class SimilarCaseSeedResult:
    """Seed 명령 실행 결과이다."""

    rule_set_id: int
    created_count: int
    skipped_count: int


def seed_agent_similar_cases(session: Session) -> SimilarCaseSeedResult:
    """4개 사기 유형별 완료·검토 사건을 생성하고 중복 사건은 건너뛴다."""

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
            if session.get(AgentCase, case_id) is not None:
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
    customer_id = f"DEMO-CUSTOMER-{suffix}"
    source_account_id = f"DEMO-SOURCE-{suffix}"
    recipient_account_id = f"DEMO-RECIPIENT-{suffix}"
    transaction_id = f"DEMO-TX-{suffix}"
    case_id = _case_id(type_index, case_index)
    occurred_at = datetime(2026, 7, 1, 9, 0, tzinfo=UTC) + timedelta(
        days=type_index * CASES_PER_TYPE + case_index
    )

    session.add(
        Customer(
            customer_id=customer_id,
            birth_date=date(1960 + case_index * 5, 1, 1),
            gender="female" if case_index % 2 == 0 else "male",
            personal_identifier=f"시연고객-{suffix}",
            identification_number=f"DEMO-ID-{suffix}",
            registration_datetime=occurred_at - timedelta(days=365),
            credit_rating=3 + case_index,
            loan_type="a",
        )
    )
    # 관계 객체 없이 FK 값만 지정하므로 PostgreSQL INSERT 순서를 명시한다.
    session.flush()
    session.add_all(
        [
            Account(
                account_id=source_account_id,
                customer_id=customer_id,
                account_number=f"DEMO-SOURCE-NUMBER-{suffix}",
                account_type="a",
                creation_datetime=occurred_at - timedelta(days=300),
                amount_daily_limit=50_000_000,
                current_balance=30_000_000,
                remaining_daily_limit=20_000_000,
            ),
            Account(
                account_id=recipient_account_id,
                account_number=f"DEMO-RECIPIENT-NUMBER-{suffix}",
                account_type="a",
                creation_datetime=occurred_at - timedelta(days=30),
            ),
        ]
    )
    session.flush()

    amounts = (15_000_000, 12_000_000, 9_000_000, 6_000_000)
    channels = ("mobile", "internet", "atm", "mobile")
    transaction_amount = amounts[case_index]
    session.add(
        Transaction(
            transaction_id=transaction_id,
            customer_id=customer_id,
            source_account_id=source_account_id,
            recipient_account_id=recipient_account_id,
            transaction_datetime=occurred_at,
            transaction_amount=transaction_amount,
            channel=channels[case_index],
            type_general_automatic="general",
            access_medium="a",
            error_code="a",
            num_connection_failure=case_index,
            another_person_account=True,
            initial_balance=30_000_000,
            balance=30_000_000 - transaction_amount,
            remaining_amount_daily_limit_exceeded=0,
            operating_system="Android",
            location="시연용 거래 위치",
            rooting_jailbreak_indicator=False,
            mobile_roaming_indicator=False,
            vpn_indicator=case_index == 2,
            flag_terminal_malicious_behavior_1=False,
            flag_terminal_malicious_behavior_2=(fraud_type == ACCOUNT_TAKEOVER),
            flag_terminal_malicious_behavior_3=False,
            flag_terminal_malicious_behavior_5=False,
            flag_terminal_malicious_behavior_6=False,
        )
    )
    session.flush()
    session.add(
        MLPredictionResult(
            transaction_id=transaction_id,
            prediction_is_fraud=True,
            fraud_probability=(0.96, 0.91, 0.86, 0.80)[case_index],
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
            fraud_type: _select_component_keys(component_keys, case_index)
        },
        created_at=occurred_at,
    )
    session.add(score_result)
    session.flush()
    if score_result.id is None:
        raise RuntimeError("시연용 Rule 결과 식별자를 생성하지 못했다.")

    # 같은 유형의 시연 사건끼리는 위험등급을 맞추고 점수 차이만 둔다.
    # 현재 사건을 제외한 동종 사건 3건이 Top 3 비교 후보가 되기 위한 구성이다.
    risk_scores = (94, 90, 86, 82)
    risk_grades = ("VERY_HIGH", "VERY_HIGH", "VERY_HIGH", "VERY_HIGH")
    completed_at = occurred_at + timedelta(seconds=5)
    session.add(
        AgentCase(
            case_id=case_id,
            transaction_id=transaction_id,
            fraud_type_score_result_id=score_result.id,
            execution_status=AgentExecutionStatus.COMPLETED.value,
            risk_score=risk_scores[case_index],
            risk_grade=risk_grades[case_index],
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
            performed_actions=[
                {
                    "action_code": "VERIFY_CUSTOMER_TRANSACTION",
                    "completed": True,
                }
            ],
            checklist_results=[
                {
                    "item_code": "CUSTOMER_CONFIRMED",
                    "checked": True,
                }
            ],
            resolution_summary=f"담당자가 {fraud_type} 유형으로 확정한 시연 사건이다.",
            reviewed_at=completed_at,
        )
    )


def _case_id(type_index: int, case_index: int) -> str:
    return f"DEMO-CASE-{type_index + 1:02d}-{case_index + 1:02d}"


def _type_scores(primary_type: str, case_index: int) -> dict[str, float]:
    primary_scores = (0.90, 0.84, 0.78, 0.72)
    scores = {fraud_type: 0.10 for fraud_type in FRAUD_TYPES}
    scores[primary_type] = primary_scores[case_index]
    secondary_index = (FRAUD_TYPES.index(primary_type) + 1) % len(FRAUD_TYPES)
    scores[FRAUD_TYPES[secondary_index]] = (0.42, 0.38, 0.34, 0.30)[case_index]
    return scores


def _select_component_keys(keys: list[str], case_index: int) -> list[str]:
    maximum = min(len(keys), 3)
    count = max(1, maximum - (case_index % maximum))
    start = case_index % len(keys)
    return [keys[(start + offset) % len(keys)] for offset in range(count)]


def main() -> None:
    with Session(engine) as session:
        result = seed_agent_similar_cases(session)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
