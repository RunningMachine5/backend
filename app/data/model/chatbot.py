"""챗봇의 세션, 대화, 추출 및 채점 기록 저장 모델"""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY, JSON_COLUMN

class ChatSessionStatus(str, Enum):
    """고객 상담 세션의 진행 상태."""

    URL_SENT = "URL_SENT" # 챗봇 URL 이 보내짐
    IN_PROGRESS = "IN_PROGRESS" # 상담 진행중
    HANDOFF_REQUESTED = "HANDOFF_REQUESTED" # 아 됐고 담당자 불러와
    DONE = "DONE" # 상담 끝
    FAILED = "FAILED" # 실패


class ChatSenderType(str, Enum):
    """상담 메시지 작성 주체."""

    AI = "AI" # AI 응답
    HUMAN = "HUMAN" # 사람이 쓴
    SYSTEM = "SYSTEM" # 뭐 오류메시지등


class ChatSession(SQLModel, table=True):
    """거래 한 건에 대한 고객 상담 세션."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", # 거래 한 건에 대하여 고객 상담은 하나다
            name="uq_chat_sessions_transaction_id",
        ),
        Index("ix_chat_sessions_status", "status"),
        CheckConstraint(
            "status IN "
            "('URL_SENT', 'IN_PROGRESS', 'HANDOFF_REQUESTED', 'DONE', 'FAILED')",
            name="ck_chat_sessions_status",
        ),
    )

    chat_session_id: str = Field(primary_key=True, max_length=64)
    transaction_id: int = Field(
        foreign_key="transactions.id",
        ondelete="CASCADE",
        sa_type=BIGINT_PRIMARY_KEY,
    )
    status: str = Field(default=ChatSessionStatus.URL_SENT.value, max_length=32)
    # 현재 '세션에 기록된 마지막 채팅 id' '채팅 마지막 순번 id'는 서로 참조하므로 FK를 미리 박을수가 없는 구조
    # use_alter=True는 두 테이블을 먼저 만든 후 last_message_id FK를 ALTER TABLE로 추가하도록 지시
    last_message_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            ForeignKey(
                "chat_messages.message_id",
                ondelete="SET NULL", # cascade 아님
                use_alter=True,
                name="fk_chat_sessions_last_message_id",
            ),
            nullable=True,
        ),
    )
    # 60세이상인가요? 노인전용 UI 를 위해
    is_older: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    # 룰 채점 점수 내림차순 상위 2개 사기유형 코드. 1step 유형판별 질문 선택에 쓴다.
    # 룰 채점 실패로 값이 없으면(NULL) 유형판별 질문 대신 일반 질문 폴백을 쓴다.
    top_fraud_types: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON_COLUMN, nullable=True),
    )
    # 질문 스텝
    # 첫 질문은 유형별 질문을 하기 위함
    question_step: int = Field(
        default=0,
        sa_column=Column(
            Integer,
            nullable=False,
            server_default=text("0"),
        ),
    )
    # 챗봇 접속 URL 이메일이 성공적으로 발송된 시각
    email_sent_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # 실제 발생 대상 주소
    notified_email: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    # 상담 종료 시각
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ChatMessage(SQLModel, table=True):
    """상담 세션에 쌓이는 개별 메시지."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index(
            "ix_chat_messages_session_sent_at",
            "chat_session_id",
            "sent_at",
        ),
        CheckConstraint(
            "sender_type IN ('AI', 'HUMAN', 'SYSTEM')",
            name="ck_chat_messages_sender_type",
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

    # 어떤 세션에 속한 메시지인지
    chat_session_id: str = Field(
        foreign_key="chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    # 메시지 발화 주체 (AI/사람/시스템)
    sender_type: str = Field(max_length=16)
    # 메시지 원문
    message_text: str = Field(sa_column=Column(Text, nullable=False))
    # 전송 시간
    sent_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

class ChatAnswer(SQLModel, table=True):
    """질문별 고객 답변의 시도 횟수와 평가 결과를 기록한다."""

    __tablename__ = "chat_answers"
    __table_args__ = (
        # 같은 세션의 같은 질문에서 동일한 재시도 번호가 중복 저장되는 것을 막는다.
        # 요청 재처리나 LangGraph 노드 재실행 시에도 시도 이력이 한 건만 남는다.
        UniqueConstraint(
            "chat_session_id",
            "question_step",
            "attempt_no",
            name="uq_chat_answers_session_step_attempt",
        ),
        # 하나의 채팅 메시지가 여러 답변 시도 레코드에 연결되는 것을 막는다.
        UniqueConstraint(
            "message_id",
            name="uq_chat_answers_message_id",
        ),
        # 세션의 특정 질문에 속한 모든 시도 이력을 빠르게 조회하기 위한 인덱스다.
        Index(
            "ix_chat_answers_session_step",
            "chat_session_id",
            "question_step",
        ),
        # 한 질문에서 최종 채택된 답변은 최대 한 건이어야 한다.
        # is_adopted가 false인 재시도 이력은 여러 건 저장할 수 있도록 부분 유니크 인덱스를 쓴다.
        Index(
            "uq_chat_answers_adopted",
            "chat_session_id",
            "question_step",
            unique=True,
            postgresql_where=text("is_adopted"),
            sqlite_where=text("is_adopted = 1"),
        ),
        # 최초 답변 1회와 재질문 2회만 허용하는 정책을 DB에서도 강제한다.
        CheckConstraint(
            "attempt_no BETWEEN 1 AND 3",
            name="ck_chat_answers_attempt_no",
        ),
        # LLM 평가 결과에 정의되지 않은 문자열이 저장되는 것을 막는다.
        # 평가를 수행하지 못한 경우를 표현하기 위해 NULL은 허용한다.
        CheckConstraint(
            "quality_verdict IS NULL OR quality_verdict IN "
            "('SUFFICIENT', 'TOO_VAGUE', 'WANT_END')",
            name="ck_chat_answers_quality_verdict",
        ),
        # 평가 결과가 NULL일 때 기록할 수 있는 생략 사유를 정해진 값으로 제한한다.
        # 정상적으로 평가된 답변은 생략 사유가 없으므로 NULL을 허용한다.
        CheckConstraint(
            "verdict_skip_reason IS NULL OR verdict_skip_reason IN "
            "('MAX_RETRY_EXCEEDED', 'EVALUATOR_FAILED')",
            name="ck_chat_answers_verdict_skip_reason",
        ),
    )

    answer_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    chat_session_id: str = Field(
        foreign_key="chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    # 몇번째 질문단계에서의 응답이였는지
    question_step: int = Field(sa_column=Column(Integer, nullable=False))
    # 몇번째 재시도중인건지
    attempt_no: int = Field(sa_column=Column(Integer, nullable=False))
    # 메시지 원래 id
    message_id: int = Field(
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            ForeignKey("chat_messages.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    # 답변 품질 평가 결과
    quality_verdict: str | None = Field(default=None, max_length=16)
    # 답변 평가하지못해 quality_verdict 을 갱신하지 못한 이유 MAX_RETRY_EXCEEDED(재질문 한도를 초과해 평가를 생략),LLM 오류 등
    verdict_skip_reason: str | None = Field(default=None, max_length=24)
    # 최종적으로 선정된 답변인지, 답변 평가 로직 때문에 존재
    is_adopted: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=false(),
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ChatGuideSearchQuery(SQLModel, table=True):
    """고객 답변에서 분해한 가이드 검색 질의를 저장한다."""

    __tablename__ = "chat_guide_search_queries"
    __table_args__ = (
        UniqueConstraint(
            "source_answer_id",
            "position",
            name="uq_chat_guide_search_queries_answer_position",
        ),
        CheckConstraint(
            "position BETWEEN 1 AND 5",
            name="ck_chat_guide_search_queries_position",
        ),
    )

    guide_search_query_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    chat_session_id: str = Field(
        foreign_key="chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    # 한 고객 답변에서 분해된 가이드 검색 질의의 순서. 1부터 시작하며 최대 5까지 허용한다.
    position: int = Field(sa_column=Column(Integer, nullable=False))
    # 고객 응답에 표시할 가이드 검색 질의별 소제목
    title: str = Field(max_length=120)
    # LLM이 만들어 준 벡터 검색에 바로 사용할 수 있는 질의
    search_query: str = Field(sa_column=Column(Text, nullable=False))
    # 이 검색 질의를 생성한 근거로, 고객 답변에 실제로 존재하는 연속된 원문이다.
    evidence: str = Field(sa_column=Column(Text, nullable=False))
    # 어떤 고객 답변에서 분해됐는지 추적한다.
    source_answer_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            ForeignKey(
                "chat_answers.answer_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ChatFraudCircumstance(SQLModel, table=True):
    """고객 답변에서 추출한 사기 정황을 저장한다."""

    __tablename__ = "chat_fraud_circumstances"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id",
            "circumstance_code",
            name="uq_chat_fraud_circumstances_session_code",
        ),
    )

    # 상황이 영어로 circumstance
    circumstance_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    chat_session_id: str = Field(
        foreign_key="chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    # fraud_circumstance(상황) enum 코드다.
    circumstance_code: str = Field(max_length=64)
    # 추출 판단의 근거가 된 고객 답변의 연속된 원문이다.
    evidence: str = Field(sa_column=Column(Text, nullable=False))
    # 어떤 채택 답변에서 추출했는지 연결하며, 답변 삭제 시 추출 기록은 보존한다.
    source_answer_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            ForeignKey(
                "chat_answers.answer_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FraudTypeScoreAfterChat(SQLModel, table=True):
    """챗봇 대화에서 집계한 사기유형별 점수."""

    __tablename__ = "fraud_type_score_after_chat"

    transaction_id: int = Field(
        primary_key=True,
        foreign_key="transactions.id",
        ondelete="CASCADE",
        sa_type=BIGINT_PRIMARY_KEY,
    )
    chat_session_id: str = Field(
        foreign_key="chat_sessions.chat_session_id",
        ondelete="CASCADE",
        max_length=64,
    )
    type_scores: dict[str, float] = Field(
        default_factory=dict,
        sa_column=Column(
            JSON_COLUMN,
            nullable=False,
            server_default=text("'{}'"), # 기본값은 {}
        ),
    )
    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
