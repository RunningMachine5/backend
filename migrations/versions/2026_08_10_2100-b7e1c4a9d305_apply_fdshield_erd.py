"""apply fdshield ERD

FDShield ERD(DBML)를 스키마에 반영한다.

* transactions.raw_features JSONB를 평탄 컬럼과 derived_features로 분해
* customer_events, derived_features, agent_* 6종 신규 생성
* documents/document_chunks에 ERD 컬럼 추가
* ERD에 없는 컬럼(shap, training_runs 확장 필드, split_datetime) 제거

Revision ID: b7e1c4a9d305
Revises: a3f8c1d7e920
Create Date: 2026-08-10 21:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b7e1c4a9d305"
down_revision: Union[str, Sequence[str], None] = "a3f8c1d7e920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())

# raw_features는 MLTransactionFeatures.model_dump(by_alias=True) 결과라
# 정지해제는 오타 키(Account_release_suspention), 시간차는 'Time Difference'다.
RELEASE_SUSPENSION_KEY = "Account_release_suspention"
TIME_DIFFERENCE_KEY = "Time Difference"

# 'ㅇㅇ시 ㅇㅇ구 37.5665 126.9780' 끝의 위도·경도 두 값을 뽑는다.
LOCATION_REGEX = r"([-+]?[0-9]*\.?[0-9]+)\s+([-+]?[0-9]*\.?[0-9]+)\s*$"


def _flag(key: str) -> str:
    """raw_features의 0/1 정수 플래그를 boolean 표현식으로 바꾼다."""

    return f"(raw_features->>'{key}')::int::boolean"


def upgrade() -> None:
    _upgrade_customers()
    _upgrade_accounts()
    _upgrade_transactions()
    _create_customer_events()
    _create_derived_features()
    _upgrade_fraud_rules()
    _create_agent_tables()
    _upgrade_documents()
    _upgrade_ml_and_mlops()


# ---------------------------------------------------------------- customers


def _upgrade_customers() -> None:
    """birth_date를 birthyear로 낮추고 loan_type을 raw_features에서 채운다."""

    op.add_column("customers", sa.Column("birthyear", sa.SmallInteger(), nullable=True))
    op.add_column("customers", sa.Column("loan_type", sa.String(length=8), nullable=True))
    op.add_column(
        "customers",
        sa.Column("credit_rating_int", sa.SmallInteger(), nullable=True),
    )

    op.execute(
        "UPDATE customers SET birthyear = EXTRACT(YEAR FROM birth_date)::smallint"
    )
    # credit_rating은 varchar로 저장돼 있었고 값은 항상 1~9 숫자 문자열이다.
    op.execute(
        "UPDATE customers SET credit_rating_int = NULLIF(credit_rating, '')::smallint"
    )
    # loan_type은 customers에 없던 값이라 해당 고객의 거래 원본에서 가져온다.
    op.execute(
        """
        UPDATE customers AS c
        SET loan_type = sub.loan_type
        FROM (
            SELECT DISTINCT ON (customer_id)
                   customer_id,
                   raw_features->>'Customer_loan_type' AS loan_type
            FROM transactions
            ORDER BY customer_id, transaction_datetime DESC
        ) AS sub
        WHERE c.customer_id = sub.customer_id
          AND sub.loan_type IS NOT NULL
        """
    )
    # 거래가 한 건도 없는 고객은 출처가 없다. ERD가 NOT NULL이라 'a'로 채운다.
    op.execute("UPDATE customers SET loan_type = 'a' WHERE loan_type IS NULL")

    op.drop_column("customers", "birth_date")
    op.drop_column("customers", "credit_rating")
    op.alter_column("customers", "credit_rating_int", new_column_name="credit_rating")

    op.alter_column("customers", "birthyear", nullable=False)
    op.alter_column("customers", "loan_type", nullable=False)
    op.alter_column("customers", "credit_rating", nullable=False)

    op.create_check_constraint(
        "ck_customers_credit_rating", "customers", "credit_rating BETWEEN 1 AND 9"
    )
    op.create_check_constraint(
        "ck_customers_loan_type", "customers", "loan_type IN ('a','b','c','d','e')"
    )
    op.create_check_constraint(
        "ck_customers_gender", "customers", "gender IN ('male','female')"
    )


# ----------------------------------------------------------------- accounts


def _upgrade_accounts() -> None:
    """ERD의 계좌 한도·오픈뱅킹·정지 상태 컬럼을 추가하고 백필한다."""

    for column in (
        sa.Column("amount_daily_limit", sa.BigInteger(), nullable=True),
        sa.Column("indicator_openbanking", sa.Boolean(), nullable=True),
        sa.Column("indicator_release_limit_excess", sa.Boolean(), nullable=True),
        sa.Column("current_balance", sa.BigInteger(), nullable=True),
        sa.Column("remaining_daily_limit", sa.BigInteger(), nullable=True),
        sa.Column(
            "suspend_status",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    ):
        op.add_column("accounts", column)
    op.alter_column("accounts", "suspend_status", server_default=None)

    # 출금 계좌만 원본이 있다. 가장 최근 거래의 값을 현재 상태로 삼는다.
    op.execute(
        """
        UPDATE accounts AS a
        SET amount_daily_limit = (t.raw_features->>'Account_amount_daily_limit')::bigint,
            indicator_openbanking =
                (t.raw_features->>'Account_indicator_Openbanking')::int::boolean,
            indicator_release_limit_excess =
                (t.raw_features->>'Account_indicator_release_limit_excess')::int::boolean,
            current_balance = (t.raw_features->>'Account_balance')::bigint,
            remaining_daily_limit =
                (t.raw_features->>'Account_remaining_amount_daily_limit_exceeded')::bigint
        FROM (
            SELECT DISTINCT ON (source_account_id) source_account_id, raw_features
            FROM transactions
            ORDER BY source_account_id, transaction_datetime DESC
        ) AS t
        WHERE a.account_id = t.source_account_id
        """
    )
    # 수취 계좌 정지 여부는 거래 스냅샷에만 남아 있다.
    op.execute(
        """
        UPDATE accounts AS a
        SET suspend_status = true
        FROM (
            SELECT DISTINCT ON (recipient_account_id) recipient_account_id, raw_features
            FROM transactions
            WHERE recipient_account_id IS NOT NULL
            ORDER BY recipient_account_id, transaction_datetime DESC
        ) AS t
        WHERE a.account_id = t.recipient_account_id
          AND (t.raw_features->>'Recipient_account_suspend_status')::int = 1
        """
    )

    op.create_check_constraint(
        "ck_accounts_account_type",
        "accounts",
        "account_type IS NULL OR account_type IN ('a','b','c','d')",
    )


# ------------------------------------------------------------- transactions

_TRANSACTION_TEXT_COLUMNS = (
    ("type_general_automatic", "Type_General_Automatic", sa.String(length=16)),
    ("access_medium", "Access_Medium", sa.String(length=8)),
    ("error_code", "Error_Code", sa.String(length=8)),
    ("operating_system", "Operating_System", sa.String(length=32)),
)

_TRANSACTION_BIGINT_COLUMNS = (
    ("initial_balance", "Account_initial_balance"),
    ("balance", "Account_balance"),
    (
        "remaining_amount_daily_limit_exceeded",
        "Account_remaining_amount_daily_limit_exceeded",
    ),
)

_TRANSACTION_FLAG_COLUMNS = (
    ("another_person_account", "Another_Person_Account"),
    ("rooting_jailbreak_indicator", "Customer_rooting_jailbreak_indicator"),
    ("mobile_roaming_indicator", "Customer_mobile_roaming_indicator"),
    ("vpn_indicator", "Customer_VPN_Indicator"),
    (
        "flag_terminal_malicious_behavior_1",
        "Customer_flag_terminal_malicious_behavior_1",
    ),
    (
        "flag_terminal_malicious_behavior_2",
        "Customer_flag_terminal_malicious_behavior_2",
    ),
    (
        "flag_terminal_malicious_behavior_3",
        "Customer_flag_terminal_malicious_behavior_3",
    ),
    (
        "flag_terminal_malicious_behavior_5",
        "Customer_flag_terminal_malicious_behavior_5",
    ),
    (
        "flag_terminal_malicious_behavior_6",
        "Customer_flag_terminal_malicious_behavior_6",
    ),
)


def _upgrade_transactions() -> None:
    """raw_features JSONB를 ERD의 평탄 컬럼으로 분해한다."""

    for name, _, column_type in _TRANSACTION_TEXT_COLUMNS:
        op.add_column("transactions", sa.Column(name, column_type, nullable=True))
    for name, _ in _TRANSACTION_BIGINT_COLUMNS:
        op.add_column("transactions", sa.Column(name, sa.BigInteger(), nullable=True))
    for name, _ in _TRANSACTION_FLAG_COLUMNS:
        op.add_column("transactions", sa.Column(name, sa.Boolean(), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("num_connection_failure", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "transactions", sa.Column("ip_address", postgresql.INET(), nullable=True)
    )
    op.add_column(
        "transactions", sa.Column("mac_address", postgresql.MACADDR(), nullable=True)
    )
    op.add_column("transactions", sa.Column("location_lat", sa.Float(), nullable=True))
    op.add_column("transactions", sa.Column("location_lon", sa.Float(), nullable=True))

    assignments = [
        f"{name} = raw_features->>'{key}'" for name, key, _ in _TRANSACTION_TEXT_COLUMNS
    ]
    assignments += [
        f"{name} = (raw_features->>'{key}')::bigint"
        for name, key in _TRANSACTION_BIGINT_COLUMNS
    ]
    assignments += [
        f"{name} = {_flag(key)}" for name, key in _TRANSACTION_FLAG_COLUMNS
    ]
    assignments.append(
        "num_connection_failure = "
        "(raw_features->>'Transaction_num_connection_failure')::smallint"
    )
    assignments.append(
        f"location_lat = (regexp_match(location, '{LOCATION_REGEX}'))[1]::double precision"
    )
    assignments.append(
        f"location_lon = (regexp_match(location, '{LOCATION_REGEX}'))[2]::double precision"
    )
    op.execute("UPDATE transactions SET " + ", ".join(assignments))

    for name, _, _ in _TRANSACTION_TEXT_COLUMNS:
        op.alter_column("transactions", name, nullable=False)
    for name, _ in _TRANSACTION_BIGINT_COLUMNS:
        op.alter_column("transactions", name, nullable=False)
    for name, _ in _TRANSACTION_FLAG_COLUMNS:
        op.alter_column("transactions", name, nullable=False)
    op.alter_column("transactions", "num_connection_failure", nullable=False)

    op.create_index(
        "ix_transactions_customer_id_transaction_datetime",
        "transactions",
        ["customer_id", "transaction_datetime"],
    )
    op.create_index(
        "ix_transactions_transaction_datetime",
        "transactions",
        ["transaction_datetime"],
    )
    # 단일 customer_id 인덱스는 위 복합 인덱스의 접두사라 중복이다.
    op.drop_index("ix_transactions_customer_id", table_name="transactions")

    for name, expression in (
        ("channel", "channel IN ('mobile','internet','ATM','Others')"),
        (
            "type_general_automatic",
            "type_general_automatic IN ('general','automatic')",
        ),
        ("access_medium", "access_medium IN ('a','b','c','d','e','f','g','h')"),
        ("error_code", "error_code IN ('a','b','c','d','e','f')"),
        ("location_lat", "location_lat IS NULL OR location_lat BETWEEN -90 AND 90"),
        ("location_lon", "location_lon IS NULL OR location_lon BETWEEN -180 AND 180"),
        ("num_connection_failure", "num_connection_failure >= 0"),
    ):
        op.create_check_constraint(
            f"ck_transactions_{name}", "transactions", expression
        )


# -------------------------------------------------------- customer_events


def _create_customer_events() -> None:
    op.create_table(
        "customer_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.account_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        # 같은 이벤트를 재주입해도 윈도우 집계가 흔들리지 않게 막는다.
        sa.UniqueConstraint(
            "customer_id",
            "event_type",
            "occurred_at",
            name="uq_customer_events_natural_key",
        ),
    )
    op.create_index(
        "ix_customer_events_customer_id_occurred_at",
        "customer_events",
        ["customer_id", "occurred_at"],
    )
    op.create_index(
        "ix_customer_events_event_type_occurred_at",
        "customer_events",
        ["event_type", "occurred_at"],
    )
    op.create_index(
        "ix_customer_events_account_id", "customer_events", ["account_id"]
    )


# -------------------------------------------------------- derived_features

_DERIVED_FLAG_COLUMNS = (
    ("unused_terminal_status", "Unused_terminal_status"),
    ("unused_account_status", "Unused_account_status"),
    ("flag_deposit_more_than_tenmillion", "Flag_deposit_more_than_tenMillion"),
    ("flag_change_of_authentication_1", "Customer_flag_change_of_authentication_1"),
    ("flag_change_of_authentication_2", "Customer_flag_change_of_authentication_2"),
    ("flag_change_of_authentication_3", "Customer_flag_change_of_authentication_3"),
    ("flag_change_of_authentication_4", "Customer_flag_change_of_authentication_4"),
    ("inquiry_atm_limit", "Customer_inquery_atm_limit"),
    ("increase_atm_limit", "Customer_increase_atm_limit"),
    ("release_suspension", RELEASE_SUSPENSION_KEY),
    ("recipient_account_suspend_status", "Recipient_account_suspend_status"),
    ("first_time_ios_by_vulnerable_user", "First_time_iOS_by_vulnerable_user"),
)


def _create_derived_features() -> None:
    op.create_table(
        "derived_features",
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("distance", sa.Float(), nullable=False),
        sa.Column("time_difference", postgresql.INTERVAL(), nullable=False),
        sa.Column("one_month_max_amount", sa.BigInteger(), nullable=False),
        sa.Column("one_month_std_dev", sa.Float(), nullable=False),
        sa.Column("dawn_one_month_max_amount", sa.BigInteger(), nullable=False),
        sa.Column("dawn_one_month_std_dev", sa.Float(), nullable=False),
        sa.Column("unused_terminal_status", sa.Boolean(), nullable=False),
        sa.Column("unused_account_status", sa.Boolean(), nullable=False),
        sa.Column("flag_deposit_more_than_tenmillion", sa.Boolean(), nullable=False),
        sa.Column(
            "number_of_transaction_with_the_account", sa.Integer(), nullable=False
        ),
        sa.Column(
            "transaction_history_with_the_account", sa.Integer(), nullable=False
        ),
        sa.Column(
            "last_atm_transaction_datetime", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_bank_branch_transaction_datetime",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("flag_change_of_authentication_1", sa.Boolean(), nullable=False),
        sa.Column("flag_change_of_authentication_2", sa.Boolean(), nullable=False),
        sa.Column("flag_change_of_authentication_3", sa.Boolean(), nullable=False),
        sa.Column("flag_change_of_authentication_4", sa.Boolean(), nullable=False),
        sa.Column("inquiry_atm_limit", sa.Boolean(), nullable=False),
        sa.Column("increase_atm_limit", sa.Boolean(), nullable=False),
        sa.Column("release_suspension", sa.Boolean(), nullable=False),
        sa.Column(
            "transaction_resumed_date", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("recipient_account_suspend_status", sa.Boolean(), nullable=False),
        sa.Column("first_time_ios_by_vulnerable_user", sa.Boolean(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )

    flag_selects = ",\n            ".join(
        _flag(key) for _, key in _DERIVED_FLAG_COLUMNS
    )
    flag_names = ",\n            ".join(name for name, _ in _DERIVED_FLAG_COLUMNS)
    op.execute(
        f"""
        INSERT INTO derived_features (
            transaction_id,
            distance,
            time_difference,
            one_month_max_amount,
            one_month_std_dev,
            dawn_one_month_max_amount,
            dawn_one_month_std_dev,
            number_of_transaction_with_the_account,
            transaction_history_with_the_account,
            last_atm_transaction_datetime,
            last_bank_branch_transaction_datetime,
            transaction_resumed_date,
            {flag_names},
            computed_at
        )
        SELECT
            transaction_id,
            (raw_features->>'Distance')::double precision,
            (raw_features->>'{TIME_DIFFERENCE_KEY}')::interval,
            (raw_features->>'Account_one_month_max_amount')::bigint,
            (raw_features->>'Account_one_month_std_dev')::double precision,
            (raw_features->>'Account_dawn_one_month_max_amount')::bigint,
            (raw_features->>'Account_dawn_one_month_std_dev')::double precision,
            (raw_features->>'Number_of_transaction_with_the_account')::int,
            (raw_features->>'Transaction_history_with_the_account')::int,
            NULLIF(raw_features->>'Last_atm_transaction_datetime', '')::timestamptz,
            NULLIF(
                raw_features->>'Last_bank_branch_transaction_datetime', ''
            )::timestamptz,
            NULLIF(raw_features->>'Transaction_resumed_date', '')::timestamptz,
            {flag_selects},
            created_at
        FROM transactions
        """
    )

    # 평탄 컬럼과 derived_features로 모두 옮긴 뒤에만 원본 JSONB를 버린다.
    op.drop_column("transactions", "raw_features")


# --------------------------------------------------------- rule engine


def _upgrade_fraud_rules() -> None:
    """ERD에 없는 CHECK를 제거하고 판정 결과 컬럼을 추가한다."""

    op.drop_constraint("ck_fraud_rules_sort_order", "fraud_rules", type_="check")
    op.drop_constraint(
        "ck_fraud_rule_components_sort_order", "fraud_rule_components", type_="check"
    )
    op.drop_constraint(
        "ck_fraud_rule_components_weight", "fraud_rule_components", type_="check"
    )

    op.add_column(
        "fraud_type_score_results",
        sa.Column("rule_filter_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "fraud_type_score_results",
        sa.Column("primary_fraud_type", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_fraud_type_score_results_primary_fraud_type",
        "fraud_type_score_results",
        ["primary_fraud_type"],
    )

    # ERD가 모든 서로게이트 키를 bigint로 규정한다.
    for table, column in (
        ("fraud_rule_sets", "id"),
        ("fraud_rules", "id"),
        ("fraud_rules", "rule_set_id"),
        ("fraud_rule_components", "id"),
        ("fraud_rule_components", "rule_id"),
        ("fraud_type_score_results", "id"),
        ("fraud_type_score_results", "rule_set_id"),
    ):
        op.alter_column(table, column, type_=sa.BigInteger())


# --------------------------------------------------------------- agent


def _create_agent_tables() -> None:
    op.create_table(
        "agent_cases",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("fraud_type_score_result_id", sa.BigInteger(), nullable=False),
        sa.Column("execution_status", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_grade", sa.String(length=16), nullable=True),
        sa.Column("investigation_result", JSONB, nullable=True),
        sa.Column("best_similar_case_id", sa.String(length=64), nullable=True),
        sa.Column("similar_case_results", JSONB, nullable=True),
        sa.Column("response_result", JSONB, nullable=True),
        sa.Column("generation_metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fraud_type_score_result_id"],
            ["fraud_type_score_results.id"],
            ondelete="CASCADE",
        ),
        # 참조된 사건 때문에 삭제가 막히지 않도록 SET NULL로 끊는다.
        sa.ForeignKeyConstraint(
            ["best_similar_case_id"], ["agent_cases.case_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("case_id"),
        sa.UniqueConstraint("transaction_id", name="uq_agent_cases_transaction_id"),
        sa.UniqueConstraint(
            "fraud_type_score_result_id",
            name="uq_agent_cases_fraud_type_score_result_id",
        ),
        sa.CheckConstraint(
            "execution_status IN ('PROCESSING','COMPLETED','FAILED')",
            name="ck_agent_cases_execution_status",
        ),
        sa.CheckConstraint(
            "risk_grade IS NULL OR risk_grade IN ('LOW','MEDIUM','HIGH','VERY_HIGH')",
            name="ck_agent_cases_risk_grade",
        ),
        sa.CheckConstraint(
            "risk_score IS NULL OR risk_score BETWEEN 0 AND 100",
            name="ck_agent_cases_risk_score",
        ),
    )
    op.create_index(
        "ix_agent_cases_execution_status_created_at",
        "agent_cases",
        ["execution_status", "created_at"],
    )
    op.create_index("ix_agent_cases_risk_grade", "agent_cases", ["risk_grade"])

    op.create_table(
        "agent_reviews",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("reviewer_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confirmed_fraud_type", sa.String(length=64), nullable=True),
        sa.Column("performed_actions", JSONB, nullable=True),
        sa.Column("checklist_results", JSONB, nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["agent_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )

    op.create_table(
        "agent_dashboard_insights",
        sa.Column("insight_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("chart_spec", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("insight_id"),
    )
    op.create_index(
        "ix_agent_dashboard_insights_created_at",
        "agent_dashboard_insights",
        ["created_at"],
    )

    op.create_table(
        "agent_chat_sessions",
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_session_id"),
        sa.UniqueConstraint(
            "transaction_id", name="uq_agent_chat_sessions_transaction_id"
        ),
        sa.CheckConstraint(
            "status IN ('WAITING','IN_PROGRESS','HANDED_OFF','DONE','CLOSED')",
            name="ck_agent_chat_sessions_status",
        ),
    )
    op.create_index("ix_agent_chat_sessions_status", "agent_chat_sessions", ["status"])

    op.create_table(
        "agent_chat_messages",
        sa.Column("message_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("sender_type", sa.String(length=16), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["agent_chat_sessions.chat_session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id"),
        sa.CheckConstraint(
            "sender_type IN ('AI','HUMAN','SYSTEM')",
            name="ck_agent_chat_messages_sender_type",
        ),
    )
    op.create_index(
        "ix_agent_chat_messages_session_sent_at",
        "agent_chat_messages",
        ["chat_session_id", "sent_at"],
    )

    op.create_table(
        "fraud_type_score_after_chat",
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("additional_type_scores", JSONB, nullable=False),
        sa.Column("primary_fraud_type", sa.String(length=64), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )


# ------------------------------------------------------------------- RAG


def _upgrade_documents() -> None:
    op.add_column(
        "documents", sa.Column("document_key", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_type", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("fraud_type", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column(
            "audience",
            sa.String(length=16),
            nullable=False,
            server_default="COMMON",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.alter_column("documents", "audience", server_default=None)
    op.alter_column("documents", "metadata", server_default=None)

    # 기존 행에는 업무 키가 없다. title은 중복될 수 있어 id로 고유하게 만든다.
    op.execute("UPDATE documents SET document_key = 'doc-' || id::text")
    op.alter_column("documents", "document_key", nullable=False)
    op.create_unique_constraint(
        "uq_documents_document_key", "documents", ["document_key"]
    )
    op.create_check_constraint(
        "ck_documents_audience",
        "documents",
        "audience IN ('MONITORING','CUSTOMER','COMMON')",
    )

    op.add_column(
        "document_chunks",
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.alter_column("document_chunks", "metadata", server_default=None)
    # 기존 chunk_index에는 페이지 번호가 들어 있어 한 문서에서 중복될 수 있다.
    # 페이지는 metadata로 옮기고 문서 내 단조 증가 순번으로 다시 매긴다.
    op.execute(
        """
        UPDATE document_chunks AS c
        SET metadata = jsonb_build_object('page', c.chunk_index),
            chunk_index = renumbered.new_index
        FROM (
            SELECT id,
                   (ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY id) - 1)
                       AS new_index
            FROM document_chunks
        ) AS renumbered
        WHERE c.id = renumbered.id
        """
    )
    op.create_unique_constraint(
        "uq_document_chunks_document_id_chunk_index",
        "document_chunks",
        ["document_id", "chunk_index"],
    )

    op.alter_column("documents", "id", type_=sa.BigInteger())
    op.alter_column("document_chunks", "id", type_=sa.BigInteger())
    op.alter_column("document_chunks", "document_id", type_=sa.BigInteger())
    op.alter_column(
        "documents", "created_at", type_=sa.DateTime(timezone=True)
    )
    op.alter_column(
        "documents", "updated_at", type_=sa.DateTime(timezone=True)
    )
    op.alter_column(
        "document_chunks", "created_at", type_=sa.DateTime(timezone=True)
    )

    # 문서를 지우면 청크도 함께 지워지도록 FK에 CASCADE를 붙인다.
    op.drop_constraint(
        "document_chunks_document_id_fkey", "document_chunks", type_="foreignkey"
    )
    op.create_foreign_key(
        "document_chunks_document_id_fkey",
        "document_chunks",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 인덱스가 없으면 RAG 질의마다 전체 청크 거리 계산(seq scan)이 발생한다.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


# --------------------------------------------------------------- ML/MLOps


def _upgrade_ml_and_mlops() -> None:
    """ERD에 없는 컬럼을 제거한다. 담긴 값은 복구할 수 없다."""

    op.drop_column("ml_prediction_results", "shap")

    op.drop_column("dataset_versions", "split_datetime")

    op.alter_column(
        "training_runs",
        "cloud_run_operation_name",
        new_column_name="cloud_run_execution_name",
    )
    for column in (
        "model_version",
        "comparison_result",
        "decision_reason",
        "serving_revision",
        "serving_operation_name",
        "updated_at",
        "decided_at",
    ):
        op.drop_column("training_runs", column)
    op.create_index("ix_training_runs_created_at", "training_runs", ["created_at"])


def downgrade() -> None:
    """스키마 구조만 되돌린다. 드롭된 컬럼의 값은 복원되지 않는다."""

    op.drop_index("ix_training_runs_created_at", table_name="training_runs")
    op.add_column(
        "training_runs",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("training_runs", "updated_at", server_default=None)
    op.add_column(
        "training_runs",
        sa.Column("serving_operation_name", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "training_runs",
        sa.Column("serving_revision", sa.String(length=255), nullable=True),
    )
    op.add_column("training_runs", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column(
        "training_runs", sa.Column("comparison_result", sa.JSON(), nullable=True)
    )
    op.add_column(
        "training_runs", sa.Column("model_version", sa.String(length=64), nullable=True)
    )
    op.alter_column(
        "training_runs",
        "cloud_run_execution_name",
        new_column_name="cloud_run_operation_name",
    )
    op.add_column(
        "dataset_versions",
        sa.Column("split_datetime", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "ml_prediction_results",
        sa.Column("shap", JSONB, nullable=False, server_default="{}"),
    )
    op.alter_column("ml_prediction_results", "shap", server_default=None)

    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_constraint(
        "document_chunks_document_id_fkey", "document_chunks", type_="foreignkey"
    )
    op.create_foreign_key(
        "document_chunks_document_id_fkey",
        "document_chunks",
        "documents",
        ["document_id"],
        ["id"],
    )
    op.drop_constraint(
        "uq_document_chunks_document_id_chunk_index",
        "document_chunks",
        type_="unique",
    )
    op.drop_column("document_chunks", "metadata")
    op.alter_column("document_chunks", "document_id", type_=sa.Integer())
    op.alter_column("document_chunks", "id", type_=sa.Integer())
    op.drop_constraint("ck_documents_audience", "documents", type_="check")
    op.drop_constraint("uq_documents_document_key", "documents", type_="unique")
    for column in ("metadata", "audience", "fraud_type", "source_type", "document_key"):
        op.drop_column("documents", column)
    op.alter_column("documents", "id", type_=sa.Integer())

    op.drop_table("fraud_type_score_after_chat")
    op.drop_table("agent_chat_messages")
    op.drop_table("agent_chat_sessions")
    op.drop_table("agent_dashboard_insights")
    op.drop_table("agent_reviews")
    op.drop_table("agent_cases")

    for table, column in (
        ("fraud_type_score_results", "rule_set_id"),
        ("fraud_type_score_results", "id"),
        ("fraud_rule_components", "rule_id"),
        ("fraud_rule_components", "id"),
        ("fraud_rules", "rule_set_id"),
        ("fraud_rules", "id"),
        ("fraud_rule_sets", "id"),
    ):
        op.alter_column(table, column, type_=sa.Integer())
    op.drop_index(
        "ix_fraud_type_score_results_primary_fraud_type",
        table_name="fraud_type_score_results",
    )
    op.drop_column("fraud_type_score_results", "primary_fraud_type")
    op.drop_column("fraud_type_score_results", "rule_filter_status")
    op.create_check_constraint(
        "ck_fraud_rule_components_weight",
        "fraud_rule_components",
        "weight > 0 AND weight <= 1",
    )
    op.create_check_constraint(
        "ck_fraud_rule_components_sort_order", "fraud_rule_components", "sort_order >= 0"
    )
    op.create_check_constraint(
        "ck_fraud_rules_sort_order", "fraud_rules", "sort_order >= 0"
    )

    # raw_features를 되살리고 평탄 컬럼·파생 테이블에서 값을 다시 모은다.
    op.add_column(
        "transactions",
        sa.Column("raw_features", JSONB, nullable=False, server_default="{}"),
    )
    op.alter_column("transactions", "raw_features", server_default=None)
    op.drop_table("derived_features")
    op.drop_table("customer_events")

    for name in (
        "channel",
        "type_general_automatic",
        "access_medium",
        "error_code",
        "location_lat",
        "location_lon",
        "num_connection_failure",
    ):
        op.drop_constraint(f"ck_transactions_{name}", "transactions", type_="check")
    op.create_index("ix_transactions_customer_id", "transactions", ["customer_id"])
    op.drop_index("ix_transactions_transaction_datetime", table_name="transactions")
    op.drop_index(
        "ix_transactions_customer_id_transaction_datetime", table_name="transactions"
    )
    for name, _, _ in _TRANSACTION_TEXT_COLUMNS:
        op.drop_column("transactions", name)
    for name, _ in _TRANSACTION_BIGINT_COLUMNS:
        op.drop_column("transactions", name)
    for name, _ in _TRANSACTION_FLAG_COLUMNS:
        op.drop_column("transactions", name)
    for name in (
        "num_connection_failure",
        "ip_address",
        "mac_address",
        "location_lat",
        "location_lon",
    ):
        op.drop_column("transactions", name)

    op.drop_constraint("ck_accounts_account_type", "accounts", type_="check")
    for column in (
        "suspend_status",
        "remaining_daily_limit",
        "current_balance",
        "indicator_release_limit_excess",
        "indicator_openbanking",
        "amount_daily_limit",
    ):
        op.drop_column("accounts", column)

    op.drop_constraint("ck_customers_gender", "customers", type_="check")
    op.drop_constraint("ck_customers_loan_type", "customers", type_="check")
    op.drop_constraint("ck_customers_credit_rating", "customers", type_="check")
    op.add_column(
        "customers", sa.Column("birth_date", sa.Date(), nullable=True)
    )
    op.execute(
        "UPDATE customers SET birth_date = make_date(birthyear::int, 1, 1)"
    )
    op.alter_column("customers", "birth_date", nullable=False)
    op.alter_column(
        "customers",
        "credit_rating",
        type_=sa.String(length=32),
        postgresql_using="credit_rating::text",
    )
    op.drop_column("customers", "loan_type")
    op.drop_column("customers", "birthyear")


__all__ = ["upgrade", "downgrade"]
