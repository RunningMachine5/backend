"""운영 화면의 시연 거래 주입 상태 계약."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DemoTransactionInjectionRequest(BaseModel):
    transaction_count: Literal[100, 500, 1000] = 100
    transactions_per_second: int = Field(default=1, ge=1, le=20)


class DemoTransactionInjectionStatus(BaseModel):
    state: Literal["IDLE", "RUNNING", "COMPLETED", "FAILED"]
    total_count: int = Field(default=100, ge=0)
    transactions_per_second: int = Field(default=1, ge=1, le=20)
    processed_count: int = Field(default=0, ge=0)
    approved_count: int = Field(default=0, ge=0)
    declined_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


__all__ = [
    "DemoTransactionInjectionRequest",
    "DemoTransactionInjectionStatus",
]
