"""7월까지의 더미 데이터(고객, 계좌, 고객이벤트, 7월까지 거래, 파생피처, 라벨)를 DB에 일괄 적재(Bulk Insert)하는 스크립트.

적재 순서 (FK 종속성 준수):
1. customers (customers.csv)
2. accounts (accounts.csv)
3. customer_events (customer_events.csv)
4. transactions (transactions_until_july.csv)
5. derived_features (derived_features_until_july.csv)
6. transaction_labels (transaction_labels.csv 중 7월 거래분)

사용법:
    python3 scripts/seed_database_until_july.py
    python3 scripts/seed_database_until_july.py --data-dir ./dummy_data
    python3 scripts/seed_database_until_july.py --truncate  # 기존 테이블 비우고 새로 적재
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlmodel import Session

import app.data.model  # noqa: F401 (모든 SQLModel 엔티티 메타데이터 등록)
from app.core.db import engine
from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.customer_event import CustomerEvent
from app.data.model.derived_features import DerivedFeatures
from app.data.model.transaction import Transaction
from app.data.model.transaction_label import TransactionLabel


def parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None

    # 전달받은 CSV는 timezone 없는 값과 마이크로초 포함 값을 함께 사용한다.
    dt = datetime.fromisoformat(v)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def parse_date(v: str | None):
    if not v:
        return None
    return datetime.strptime(v, "%Y-%m-%d").date()


def parse_bool(v: str | None) -> bool | None:
    if v is None or v == "":
        return None
    return v in ("True", "true", "1", 1, True)


def parse_interval(v: str | None) -> timedelta:
    if not v or v == "00:00:00":
        return timedelta(seconds=0)
    if "days" in v:
        parts = v.split(" days ")
        days = int(parts[0])
        h, m, s = map(int, parts[1].split(":"))
        return timedelta(days=days, hours=h, minutes=m, seconds=s)
    h, m, s = map(int, v.split(":"))
    return timedelta(hours=h, minutes=m, seconds=s)


def load_customers(filepath: str) -> list[Customer]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            items.append(
                Customer(
                    id=int(r["id"]),
                    name=r["name"],
                    birth_date=parse_date(r["birth_date"]),
                    gender=r["gender"],
                    identification_number=r["identification_number"],
                    phone_number=r["phone_number"] or None,
                    email=r["email"] or None,
                    registration_datetime=parse_dt(r["registration_datetime"]),
                    credit_rating=int(r["credit_rating"]),
                    loan_type=r["loan_type"],
                    created_at=parse_dt(r["created_at"]),
                    updated_at=parse_dt(r["updated_at"]),
                )
            )
    return items


def load_accounts(filepath: str) -> list[Account]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cid_str = r.get("customer_id", "").strip()
            items.append(
                Account(
                    id=int(r["id"]),
                    customer_id=int(cid_str) if cid_str else None,
                    account_number=r["account_number"],
                    account_type=r["account_type"] or None,
                    creation_datetime=parse_dt(r.get("creation_datetime")),
                    current_balance=int(r["current_balance"]) if r.get("current_balance") else None,
                    amount_daily_limit=int(r["amount_daily_limit"]) if r.get("amount_daily_limit") else None,
                    indicator_openbanking=parse_bool(r.get("indicator_openbanking")),
                    suspend_status=parse_bool(r.get("suspend_status")) or False,
                    created_at=parse_dt(r["created_at"]),
                    updated_at=parse_dt(r["updated_at"]),
                )
            )
    return items


def load_customer_events(filepath: str) -> list[CustomerEvent]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            items.append(
                CustomerEvent(
                    id=int(r["id"]),
                    customer_id=int(r["customer_id"]),
                    account_number=r["account_number"] or None,
                    event_type=r["event_type"],
                    occurred_at=parse_dt(r["occurred_at"]),
                    created_at=parse_dt(r["created_at"]),
                )
            )
    return items


def load_transactions(filepath: str) -> list[Transaction]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            items.append(
                Transaction(
                    id=int(r["id"]),
                    customer_id=int(r["customer_id"]) if r.get("customer_id") else None,
                    source_account_number=r["source_account_number"],
                    recipient_account_number=r["recipient_account_number"],
                    transaction_datetime=parse_dt(r["transaction_datetime"]),
                    transaction_amount=int(r["transaction_amount"]),
                    channel=r["channel"],
                    type_general_automatic=r["type_general_automatic"],
                    access_medium=r["access_medium"] or None,
                    num_connection_failure=int(r["num_connection_failure"]),
                    initial_balance=int(r["initial_balance"]) if r.get("initial_balance") else None,
                    balance=int(r["balance"]) if r.get("balance") else None,
                    operating_system=r["operating_system"] or None,
                    ip_address=r["ip_address"] or None,
                    mac_address=r["mac_address"] or None,
                    location_lat=float(r["location_lat"]) if r.get("location_lat") else None,
                    location_lon=float(r["location_lon"]) if r.get("location_lon") else None,
                    rooting_jailbreak_indicator=parse_bool(r["rooting_jailbreak_indicator"]),
                    mobile_roaming_indicator=parse_bool(r["mobile_roaming_indicator"]),
                    vpn_indicator=parse_bool(r["vpn_indicator"]),
                    flag_terminal_malicious_behavior_1=parse_bool(r["flag_terminal_malicious_behavior_1"]),
                    flag_terminal_malicious_behavior_2=parse_bool(r["flag_terminal_malicious_behavior_2"]),
                    flag_terminal_malicious_behavior_3=parse_bool(r["flag_terminal_malicious_behavior_3"]),
                    flag_terminal_malicious_behavior_5=parse_bool(r["flag_terminal_malicious_behavior_5"]),
                    flag_terminal_malicious_behavior_6=parse_bool(r["flag_terminal_malicious_behavior_6"]),
                    transaction_status=r.get("transaction_status") or "APPROVED",
                    error_code=r.get("error_code") or None,
                    created_at=parse_dt(r["created_at"]),
                )
            )
    return items


def load_derived_features(filepath: str) -> list[DerivedFeatures]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            items.append(
                DerivedFeatures(
                    id=int(r["id"]),
                    remaining_amount_daily_limit=int(r["remaining_amount_daily_limit"]),
                    distance=float(r["distance"]),
                    time_difference=parse_interval(r["time_difference"]),
                    one_month_max_amount=int(r["one_month_max_amount"]),
                    one_month_std_dev=float(r["one_month_std_dev"]),
                    dawn_one_month_max_amount=int(r["dawn_one_month_max_amount"]),
                    dawn_one_month_std_dev=float(r["dawn_one_month_std_dev"]),
                    another_person_account=parse_bool(r["another_person_account"]),
                    unused_terminal_status=parse_bool(r["unused_terminal_status"]),
                    unused_account_status=parse_bool(r["unused_account_status"]),
                    transaction_history_with_the_account=int(r["transaction_history_with_the_account"]),
                    flag_deposit_more_than_ten_million=parse_bool(r["flag_deposit_more_than_ten_million"]),
                    number_of_transaction_with_the_account=int(r["number_of_transaction_with_the_account"]),
                    last_atm_transaction_datetime=parse_dt(r.get("last_atm_transaction_datetime")),
                    last_bank_branch_transaction_datetime=parse_dt(r.get("last_bank_branch_transaction_datetime")),
                    flag_change_of_authentication_1=parse_bool(r["flag_change_of_authentication_1"]),
                    flag_change_of_authentication_2=parse_bool(r["flag_change_of_authentication_2"]),
                    flag_change_of_authentication_3=parse_bool(r["flag_change_of_authentication_3"]),
                    flag_change_of_authentication_4=parse_bool(r["flag_change_of_authentication_4"]),
                    inquery_atm_limit=parse_bool(r["inquery_atm_limit"]),
                    increase_atm_limit=parse_bool(r["increase_atm_limit"]),
                    indicator_release_limit_excess=parse_bool(r.get("indicator_release_limit_excess")),
                    recipient_release_suspension=parse_bool(r["recipient_release_suspension"]),
                    recipient_transaction_resumed_date=parse_dt(r.get("recipient_transaction_resumed_date")),
                    recipient_account_suspend_status=parse_bool(r["recipient_account_suspend_status"]),
                    computed_at=parse_dt(r["computed_at"]),
                )
            )
    return items


def load_transaction_labels(filepath: str, max_tx_id: int) -> list[TransactionLabel]:
    items = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tx_id = int(r["transaction_id"])
            if tx_id <= max_tx_id:
                items.append(
                    TransactionLabel(
                        transaction_id=tx_id,
                        confirmed_is_fraud=parse_bool(r["confirmed_is_fraud"]),
                        labeled_at=parse_dt(r["labeled_at"]),
                    )
                )
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="7월까지의 거래 및 원장 데이터 DB 적재기")
    parser.add_argument("--data-dir", default="./dummy_data", help="더미 데이터 디렉터리 경로")
    parser.add_argument("--truncate", action="store_true", help="적재 전 기존 테이블 데이터 Truncate")
    parser.add_argument("--batch-size", type=int, default=5000, help="Bulk Insert 배치 크기")
    args = parser.parse_args()

    print("================================================================================")
    print(" 📥 DB Seeding (Until 2026-07) Started")
    print(f" - Data Directory : {args.data_dir}")
    print(f" - Truncate First : {args.truncate}")
    print("================================================================================")

    cust_path = os.path.join(args.data_dir, "customers.csv")
    acc_path = os.path.join(args.data_dir, "accounts.csv")
    ev_path = os.path.join(args.data_dir, "customer_events.csv")
    tx_path = os.path.join(args.data_dir, "transactions_until_july.csv")
    df_path = os.path.join(args.data_dir, "derived_features_until_july.csv")
    lbl_path = os.path.join(args.data_dir, "transaction_labels.csv")

    with Session(engine) as session:
        if args.truncate:
            print("\n🧹 기존 테이블 데이터 비우는 중 (TRUNCATE CASCADE)...")
            session.exec(
                text(
                    "TRUNCATE TABLE transaction_labels, derived_features, transactions, "
                    "customer_events, accounts, customers CASCADE"
                )
            )
            session.commit()
            print(" 테이블 초기화 완료.")

        print("\n[1/6] customers 적재 중...")
        customers = load_customers(cust_path)
        session.bulk_save_objects(customers)
        session.commit()
        print(f"   Saved {len(customers):,} customers.")

        print("[2/6] accounts 적재 중...")
        accounts = load_accounts(acc_path)
        session.bulk_save_objects(accounts)
        session.commit()
        print(f"   Saved {len(accounts):,} accounts.")

        print("[3/6] customer_events 적재 중...")
        events = load_customer_events(ev_path)
        session.bulk_save_objects(events)
        session.commit()
        print(f"   Saved {len(events):,} customer events.")

        print("[4/6] transactions (1월~7월) 적재 중...")
        transactions = load_transactions(tx_path)
        for i in range(0, len(transactions), args.batch_size):
            session.bulk_save_objects(transactions[i : i + args.batch_size])
            session.commit()
        print(f"   Saved {len(transactions):,} transactions.")

        max_tx_id = max(t.id for t in transactions)

        print("[5/6] derived_features (1월~7월) 적재 중...")
        derived_features = load_derived_features(df_path)
        for i in range(0, len(derived_features), args.batch_size):
            session.bulk_save_objects(derived_features[i : i + args.batch_size])
            session.commit()
        print(f"   Saved {len(derived_features):,} derived features.")

        print("[6/6] transaction_labels (1월~7월) 적재 중...")
        labels = load_transaction_labels(lbl_path, max_tx_id=max_tx_id)
        for i in range(0, len(labels), args.batch_size):
            session.bulk_save_objects(labels[i : i + args.batch_size])
            session.commit()
        print(f"   Saved {len(labels):,} transaction labels.")

        # 시퀀스 번호 재조정 (PostgreSQL sequence update)
        try:
            session.exec(text(f"SELECT setval('customers_id_seq', (SELECT MAX(id) FROM customers))"))
            session.exec(text(f"SELECT setval('accounts_id_seq', (SELECT MAX(id) FROM accounts))"))
            session.exec(text(f"SELECT setval('transactions_id_seq', (SELECT MAX(id) FROM transactions))"))
            session.commit()
        except Exception:
            session.rollback()

    print("\n================================================================================")
    print(" 7월까지의 모든 데이터가 DB에 성공적으로 적재되었습니다!")
    print("================================================================================")


if __name__ == "__main__":
    main()
