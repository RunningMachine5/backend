"""메신저피싱·사기이용계좌의 실제 Agent E2E 시나리오를 실행한다.

실행 전제:
    - 백엔드와 ML 서버가 실행 중이어야 한다.
    - DB에는 ACTIVE Rule Set, 대응 가이드, 유사 완료 사건 Seed가 적재되어 있어야 한다.

실행 예시:
    docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml \
      run --rm --no-deps backend python -m app.scripts.run_agent_e2e_scenarios
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.customer_event import CustomerEvent, CustomerEventType
from app.data.model.transaction import Transaction, TransactionStatus


@dataclass(frozen=True)
class AgentE2EScenario:
    name: str
    expected_fraud_type: str
    payload: dict[str, Any]


def _synchronize_customer_event_sequence(session: Session) -> None:
    """초기 Seed가 명시적 ID를 사용한 환경에서도 이벤트를 추가한다."""
    session.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('customer_events', 'id'), "
            "COALESCE((SELECT MAX(id) FROM customer_events), 1), true)"
        )
    )


def _transaction(
    *,
    customer_id: int,
    source_account_number: str,
    recipient_account_number: str,
    occurred_at: datetime,
    amount: int,
    mac_address: str,
) -> Transaction:
    return Transaction(
        customer_id=customer_id,
        source_account_number=source_account_number,
        recipient_account_number=recipient_account_number,
        transaction_datetime=occurred_at,
        transaction_amount=amount,
        channel="mobile",
        type_general_automatic="general",
        access_medium="a",
        num_connection_failure=0,
        initial_balance=50_000_000,
        balance=50_000_000 - amount,
        operating_system="Android",
        ip_address="203.0.113.10",
        mac_address=mac_address,
        location_lat=37.5665,
        location_lon=126.9780,
        rooting_jailbreak_indicator=False,
        mobile_roaming_indicator=False,
        vpn_indicator=False,
        flag_terminal_malicious_behavior_1=False,
        flag_terminal_malicious_behavior_2=False,
        flag_terminal_malicious_behavior_3=False,
        flag_terminal_malicious_behavior_5=False,
        flag_terminal_malicious_behavior_6=False,
        transaction_status=TransactionStatus.APPROVED,
    )


def _add_customer(
    session: Session,
    *,
    tag: str,
    name: str,
    birth_date: date,
    loan_type: str = "a",
) -> Customer:
    customer = Customer(
        name=name,
        birth_date=birth_date,
        gender="female",
        identification_number=f"E2E-{tag}",
        email=f"e2e-{tag.lower()}@example.com",
        registration_datetime=datetime(2020, 1, 1, tzinfo=UTC),
        credit_rating=3,
        loan_type=loan_type,
    )
    session.add(customer)
    session.flush()
    return customer


def _add_account(
    session: Session,
    *,
    account_number: str,
    customer_id: int | None,
    suspended: bool = False,
    open_banking: bool = False,
    daily_limit: int = 25_000_000,
    current_balance: int = 50_000_000,
    creation_datetime: datetime | None = None,
) -> Account:
    account = Account(
        customer_id=customer_id,
        account_number=account_number,
        account_type="a",
        creation_datetime=creation_datetime or datetime(2020, 1, 1, tzinfo=UTC),
        current_balance=current_balance,
        amount_daily_limit=daily_limit,
        indicator_openbanking=open_banking,
        suspend_status=suspended,
    )
    session.add(account)
    session.flush()
    return account


def seed_scenarios(session: Session) -> tuple[AgentE2EScenario, ...]:
    """Rule·파생 피처 조건을 만족하는 테스트 전제 데이터를 적재한다."""
    _synchronize_customer_event_sequence(session)
    now = datetime.now(UTC).replace(microsecond=0)
    tag = now.strftime("%Y%m%d%H%M%S")

    messenger_customer = _add_customer(
        session,
        tag=f"MSG-{tag}",
        name="E2E 메신저 고객",
        birth_date=date(1950, 1, 1),
    )
    messenger_source = _add_account(
        session,
        account_number=f"E2E-MSG-SRC-{tag}",
        customer_id=messenger_customer.id,
        open_banking=True,
        daily_limit=10_000_000,
    )
    messenger_recipient = _add_account(
        session,
        account_number=f"E2E-MSG-RCP-{tag}",
        customer_id=None,
    )
    for event_type in (
        CustomerEventType.AUTH_1,
        CustomerEventType.AUTH_2,
        CustomerEventType.AUTH_3,
    ):
        session.add(
            CustomerEvent(
                customer_id=messenger_customer.id,
                account_number=messenger_source.account_number,
                event_type=event_type.value,
                occurred_at=now - timedelta(days=1),
            )
        )
    for minute in (15, 10, 5):
        session.add(
            _transaction(
                customer_id=messenger_customer.id,
                source_account_number=messenger_source.account_number,
                recipient_account_number=messenger_recipient.account_number,
                occurred_at=now - timedelta(minutes=minute),
                amount=100_000,
                mac_address=f"02:00:00:00:10:{minute:02x}",
            )
        )

    fraud_customer = _add_customer(
        session,
        tag=f"FRAUD-{tag}",
        name="E2E 사기이용계좌 고객",
        birth_date=date(1950, 1, 1),
        loan_type="e",
    )
    fraud_source = _add_account(
        session,
        account_number=f"E2E-FRAUD-SRC-{tag}",
        customer_id=fraud_customer.id,
        daily_limit=50_000_000,
        current_balance=17_583_000,
        creation_datetime=datetime(2025, 2, 2, tzinfo=UTC),
    )
    fraud_recipient_customer = _add_customer(
        session,
        tag=f"FRAUD-RCP-{tag}",
        name="E2E 거래중지 수취인",
        birth_date=date(1980, 1, 1),
    )
    fraud_recipient = _add_account(
        session,
        account_number=f"E2E-FRAUD-RCP-{tag}",
        customer_id=fraud_recipient_customer.id,
        suspended=True,
    )
    fraud_other_recipient = _add_account(
        session,
        account_number=f"E2E-FRAUD-OTH-{tag}",
        customer_id=None,
    )
    session.add(
        CustomerEvent(
            customer_id=fraud_recipient_customer.id,
            account_number=fraud_recipient.account_number,
            event_type=CustomerEventType.SUSPENSION_RELEASE.value,
            occurred_at=now - timedelta(days=1),
        )
    )
    session.add(
        _transaction(
            customer_id=fraud_customer.id,
            source_account_number=fraud_source.account_number,
            recipient_account_number=fraud_other_recipient.account_number,
            occurred_at=now - timedelta(days=1),
            amount=12_000_000,
            mac_address="02:00:00:00:20:01",
        )
    )
    session.commit()

    return (
        AgentE2EScenario(
            name="messenger_phishing",
            expected_fraud_type="MESSENGER_PHISHING",
            payload={
                "customer_id": messenger_customer.id,
                "source_account_number": messenger_source.account_number,
                "recipient_account_number": messenger_recipient.account_number,
                "transaction_datetime": now.isoformat(),
                "transaction_amount": 9_000_000,
                "channel": "mobile",
                "type_general_automatic": "general",
                "access_medium": "a",
                "num_connection_failure": 2,
                "operating_system": "Android",
                "ip_address": "203.0.113.11",
                "mac_address": "02:00:00:00:10:ff",
                "location_lat": 37.5665,
                "location_lon": 126.9780,
                "customer_rooting_jailbreak_indicator": False,
                "customer_mobile_roaming_indicator": False,
                "customer_vpn_indicator": False,
                "customer_flag_terminal_malicious_behavior_1": False,
                "customer_flag_terminal_malicious_behavior_2": True,
                "customer_flag_terminal_malicious_behavior_3": False,
                "customer_flag_terminal_malicious_behavior_5": False,
                "customer_flag_terminal_malicious_behavior_6": False,
            },
        ),
        AgentE2EScenario(
            name="fraud_used_account",
            expected_fraud_type="FRAUD_USED_ACCOUNT",
            payload={
                "customer_id": fraud_customer.id,
                "source_account_number": fraud_source.account_number,
                "recipient_account_number": fraud_recipient.account_number,
                "transaction_datetime": now.isoformat(),
                "transaction_amount": 32_000_000,
                "channel": "mobile",
                "type_general_automatic": "general",
                "access_medium": "a",
                "num_connection_failure": 0,
                "operating_system": "Android",
                "ip_address": "203.0.113.21",
                "mac_address": "02:00:00:00:20:ff",
                "location_lat": 35.1796,
                "location_lon": 129.0756,
                "customer_rooting_jailbreak_indicator": False,
                "customer_mobile_roaming_indicator": False,
                "customer_vpn_indicator": False,
                "customer_flag_terminal_malicious_behavior_1": False,
                "customer_flag_terminal_malicious_behavior_2": False,
                "customer_flag_terminal_malicious_behavior_3": False,
                "customer_flag_terminal_malicious_behavior_5": False,
                "customer_flag_terminal_malicious_behavior_6": False,
            },
        ),
    )


def _request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_agent(
    *,
    api_url: str,
    transaction_id: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = _request_json(
                f"{api_url}/api/transactions/{transaction_id}/agent-case"
            )
        except Exception:
            time.sleep(1)
            continue
        agent_case = result["data"]
        if agent_case["execution_status"] in {"COMPLETED", "FAILED"}:
            return agent_case
        time.sleep(1)
    raise TimeoutError(f"Agent 완료 대기 시간 초과: transaction_id={transaction_id}")


def run_scenarios(
    *,
    api_url: str,
    timeout_seconds: int,
    require_agent_for_all: bool = False,
) -> list[dict[str, Any]]:
    with Session(engine) as session:
        scenarios = seed_scenarios(session)

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        transaction = _request_json(
            f"{api_url}/transactions",
            method="POST",
            payload=scenario.payload,
        )
        if not transaction["predict_result"]:
            result = {
                "scenario": scenario.name,
                "transaction_id": transaction["transaction_id"],
                "pipeline_status": "SKIPPED_BY_ML_GATE",
                "reason": "ML이 정상 거래로 판정해 Rule·위험등급·Agent를 실행하지 않음",
            }
            results.append(result)
            if require_agent_for_all:
                raise AssertionError(f"ML 이상 판정 실패: {scenario.name}")
            continue

        agent_case = _wait_for_agent(
            api_url=api_url,
            transaction_id=transaction["transaction_id"],
            timeout_seconds=timeout_seconds,
        )
        type_scores = agent_case["rule_result"]["type_scores"]
        top_type = max(type_scores, key=type_scores.__getitem__)
        if top_type != scenario.expected_fraud_type:
            raise AssertionError(
                f"대표 유형 불일치: expected={scenario.expected_fraud_type}, actual={top_type}"
            )
        if agent_case["execution_status"] != "COMPLETED":
            raise AssertionError(f"Agent 실행 실패: {scenario.name}")
        if not agent_case["similar_case_results"]:
            raise AssertionError(f"유사 사례 없음: {scenario.name}")
        if agent_case["response_result"] is None:
            raise AssertionError(f"대응 계획 없음: {scenario.name}")

        results.append(
            {
                "scenario": scenario.name,
                "transaction_id": transaction["transaction_id"],
                "pipeline_status": "AGENT_COMPLETED",
                "case_id": agent_case["case_id"],
                "top_fraud_type": top_type,
                "risk_grade": agent_case["risk_grade"],
                "similar_case_count": len(agent_case["similar_case_results"]),
                "response_fraud_type": agent_case["response_result"]["applied_fraud_type"],
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api-url",
        default="http://host.docker.internal:8000",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--require-agent-for-all",
        action="store_true",
        help="모든 시나리오가 ML 게이트를 통과하지 않으면 실패 처리한다.",
    )
    args = parser.parse_args()

    result = run_scenarios(
        api_url=args.api_url.rstrip("/"),
        timeout_seconds=args.timeout_seconds,
        require_agent_for_all=args.require_agent_for_all,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
