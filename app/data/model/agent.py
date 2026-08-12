"""Agent 조사·검토·상담 영역의 영속 모델."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, JSON_COLUMN
from app.domain.agent_status import AgentExecutionStatus


class ChatSessionStatus(str, Enum):
    """고객 상담 세션의 진행 상태."""

    URL_SENT = "URL_SENT"  # 챗봇URL전송
    IN_PROGRESS = "IN_PROGRESS"  # 챗봇 상담 진행중
    HANDOFF_REQUESTED = "HANDOFF_REQUESTED"  # 상담사 연결 요청
    DONE = "DONE"  # 챗봇 상담 완료
    FAILED = "FAILED"  # 챗봇 상담 실패


class ChatSenderType(str, Enum):
    """상담 메시지 작성 주체."""

    AI = "AI"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class AgentCase(SQLModel, table=True):
    """룰 점수 결과 한 건에 대응하는 Agent 조사 사건.

    주의: transaction_id와 fraud_type_score_result_id가 각각 UNIQUE인
    독립 경로다. fraud_type_score_results.transaction_id도 UNIQUE이므로 두
    경로가 같은 거래를 가리켜야 하지만 이를 강제하는 제약은 없다. 쓰기는
    반드시 한 곳(파이프라인)에서만 수행해 두 값을 함께 채워야 한다.
    """

    __tablename__ = "agent_cases"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            name="uq_agent_cases_transaction_id",
        ),
        UniqueConstraint(
            "fraud_type_score_result_id",
            name="uq_agent_cases_fraud_type_score_result_id",
        ),
        Index(
            "ix_agent_cases_execution_status_created_at",
            "execution_status",
            "created_at",
        ),
        Index("ix_agent_cases_risk_grade", "risk_grade"),
        CheckConstraint(
            "execution_status IN ('PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_agent_cases_execution_status",
        ),
        CheckConstraint(
            "risk_grade IN "
            "('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')",
            name="ck_agent_cases_risk_grade",
        ),
        CheckConstraint(
            "risk_score BETWEEN 0 AND 100",
            name="ck_agent_cases_risk_score",
        ),
    )

    case_id: str = Field(primary_key=True, max_length=64)
    transaction_id: str = Field(
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
        max_length=64,
    )
    fraud_type_score_result_id: int = Field(
        foreign_key="fraud_type_score_results.id",
        ondelete="CASCADE",
        sa_type=BIGINT_PRIMARY_KEY,
    )
    execution_status: str = Field(
        default=AgentExecutionStatus.PROCESSING.value,
        max_length=16,
    )
    failure_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )

    risk_score: int = Field(
        sa_column=Column(Integer, nullable=False),
    )

    risk_grade: str = Field(
        max_length=16,
        nullable=False,
    )

    investigation_result: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    # 삭제된 사건이 다른 사건의 참조 때문에 남지 않도록 SET NULL로 끊는다.
    best_similar_case_id: str | None = Field(
        default=None,
        foreign_key="agent_cases.case_id",
        ondelete="SET NULL",
        max_length=64,
    )
    similar_case_results: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    response_result: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    generation_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class AgentReview(SQLModel, table=True):
    """모니터링 담당자가 사건을 검토하고 확정한 결과 (사건당 1건)."""

    __tablename__ = "agent_reviews"

    case_id: str = Field(
        primary_key=True,
        foreign_key="agent_cases.case_id",
        ondelete="CASCADE",
        max_length=64,
    )
    # 사용자 테이블이 아직 없어 FK를 걸지 않는다. 인증 도입 시 FK로 승격한다.
    reviewer_id: str = Field(max_length=64)
    decision: str = Field(max_length=32)
    confirmed_fraud_type: str | None = Field(default=None, max_length=64)
    performed_actions: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    checklist_results: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    resolution_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    reviewed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentDashboardInsight(SQLModel, table=True):
    """모니터링 대시보드에 표시할 집계 인사이트 카드."""

    __tablename__ = "agent_dashboard_insights"

    insight_id: str = Field(primary_key=True, max_length=64)
    title: str = Field(max_length=255)
    summary: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    chart_spec: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class AgentChatSession(SQLModel, table=True):
    """거래 한 건에 대한 고객 상담 세션.

    상담원 인계(HANDED_OFF) 이후에도 같은 세션을 유지하므로 거래당 1개다.
    """

    __tablename__ = "agent_chat_sessions"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            name="uq_agent_chat_sessions_transaction_id",
        ),
        Index("ix_agent_chat_sessions_status", "status"),
        CheckConstraint(
            "status IN "
            "('URL_SENT', 'IN_PROGRESS', 'HANDOFF_REQUESTED', 'DONE', 'FAILED')",
            name="ck_agent_chat_sessions_status",
        ),
    )

    chat_session_id: str = Field(primary_key=True, max_length=64)
    transaction_id: str = Field(
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
        max_length=64,
    )
    status: str = Field(default=ChatSessionStatus.URL_SENT.value, max_length=32)
    # 세션 목록에서 마지막 메시지를 보여줄 때 메시지 테이블을 정렬하지 않으려고
    # 최신 메시지를 캐시한다. agent_chat_messages와 서로 참조하는 순환 FK라
    # use_alter=True로 테이블 생성 후 ALTER로 제약을 건다.
    last_message_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            ForeignKey(
                "agent_chat_messages.message_id",
                ondelete="SET NULL",
                use_alter=True,
                name="fk_agent_chat_sessions_last_message_id",
            ),
            nullable=True,
        ),
    )
    # 노인 전용 UI(큰 글씨·단순 흐름)로 상담을 진행할지 여부.
    is_older: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    # 상담 시작 전 상위 2개 사기유형 코드
    top_fraud_types: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentChatMessage(SQLModel, table=True):
    """상담 세션에 쌓이는 개별 메시지."""

    __tablename__ = "agent_chat_messages"
    __table_args__ = (
        Index(
            "ix_agent_chat_messages_session_sent_at",
            "chat_session_id",
            "sent_at",
        ),
        CheckConstraint(
            "sender_type IN ('AI', 'HUMAN', 'SYSTEM')",
            name="ck_agent_chat_messages_sender_type",
        ),
    )

    message_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    chat_session_id: str = Field(
        foreign_key="agent_chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    sender_type: str = Field(max_length=16)
    message_text: str = Field(sa_column=Column(Text, nullable=False))
    sent_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FraudTypeScoreAfterChat(SQLModel, table=True):
    """챗봇 대화로 확정한 대표 사기유형과 그 우세 점수.

    유형별 점수를 전부 들고 있지 않고, 대화 뒤 가장 우세한 유형 하나와 그
    유형이 얼마나 더 우세한지를 나타내는 점수만 남긴다.
    """

    __tablename__ = "fraud_type_score_after_chat"

    transaction_id: str = Field(
        primary_key=True,
        foreign_key="transactions.transaction_id",
        ondelete="CASCADE",
        max_length=64,
    )
    primary_fraud_type: str | None = Field(default=None, max_length=64)
    # 대표 유형이 나머지 유형보다 얼마나 더 우세한지의 점수.
    primary_fraud_type_score: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = [
    "AgentCase",
    "AgentChatMessage",
    "AgentChatSession",
    "AgentDashboardInsight",
    "AgentExecutionStatus",
    "AgentReview",
    "ChatSenderType",
    "ChatSessionStatus",
    "FraudTypeScoreAfterChat",
]
