"""윈도우 기반 파생 피처의 원본이 되는 고객 이벤트 이력."""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class CustomerEventType(str, Enum):
    """ERD 주석의 이벤트 종류를 영문 코드로 통일한 값.

    ERD 원문은 'AUTH1~3 / PRIVACY / ATM문의 / ATM증액 / 정지해제'처럼 한글과
    영문이 섞여 있어 코드에서 다루기 어려우므로 영문 코드로 고정한다.

    주의: derived_features는 flag_change_of_authentication_1~4로 인증 변경을
    4종 관리하지만 ERD의 이벤트 종류에는 AUTH가 3종뿐이다. 네 번째 플래그를
    어떤 이벤트가 채우는지는 아직 정의되지 않았다.
    """

    AUTH_1 = "AUTH_1"
    AUTH_2 = "AUTH_2"
    AUTH_3 = "AUTH_3"
    PRIVACY = "PRIVACY"
    ATM_LIMIT_INQUIRY = "ATM_LIMIT_INQUIRY"
    ATM_LIMIT_INCREASE = "ATM_LIMIT_INCREASE"
    SUSPENSION_RELEASE = "SUSPENSION_RELEASE"


class CustomerEvent(SQLModel, table=True):
    """인증 변경·ATM 한도 조정·정지 해제 등 고객 단위 사건 한 건."""

    __tablename__ = "customer_events"
    __table_args__ = (
        Index(
            "ix_customer_events_customer_id_occurred_at",
            "customer_id",
            "occurred_at",
        ),
        Index(
            "ix_customer_events_event_type_occurred_at",
            "event_type",
            "occurred_at",
        ),
        # 같은 이벤트를 재주입해도 윈도우 집계가 흔들리지 않도록 자연키를 막는다.
        UniqueConstraint(
            "customer_id",
            "event_type",
            "occurred_at",
            name="uq_customer_events_natural_key",
        ),
    )

    event_id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    customer_id: str = Field(
        foreign_key="customers.customer_id",
        ondelete="CASCADE",
        max_length=64,
    )
    account_id: str | None = Field(
        default=None,
        foreign_key="accounts.account_id",
        ondelete="SET NULL",
        max_length=64,
        index=True,
    )
    event_type: str = Field(max_length=32)
    # 윈도우(90일 인증변경, 7일 ATM, 30일 정지해제) 판정의 기준 시각.
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["CustomerEvent", "CustomerEventType"]
