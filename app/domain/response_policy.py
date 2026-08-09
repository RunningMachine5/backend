"""Agent 대응 계획의 필수 조치와 체크리스트를 표현하는 도메인 모델이다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
VERY_HIGH = "VERY_HIGH"
RISK_GRADE_CODES = frozenset({LOW, MEDIUM, HIGH, VERY_HIGH})


class PolicyError(ValueError):
    """대응 정책 처리 중 발생하는 오류의 공통 형식이다."""


class PolicyValidationError(PolicyError):
    """정책 파일의 구조 또는 값이 올바르지 않을 때 발생한다."""


class PolicyNotFoundError(PolicyError):
    """사기 유형과 위험등급에 맞는 정책이 존재하지 않을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class PolicyAction:
    """내부 정책에서 요구하는 한 개의 대응 조치이다."""

    priority: int
    action_code: str
    action: str
    reason: str
    required: bool


@dataclass(frozen=True, slots=True)
class PolicyChecklistItem:
    """모니터링 담당자가 확인해야 하는 한 개의 항목이다."""

    item_code: str
    label: str
    required: bool


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    """사기 유형과 위험등급 조합에 적용되는 결정론적 대응 정책이다."""

    policy_id: str
    fraud_type: str
    risk_grade: str
    notification_required: bool
    notification_reason: str | None
    actions: tuple[PolicyAction, ...]
    checklist: tuple[PolicyChecklistItem, ...]


class PolicyRepository(Protocol):
    """저장 형식과 무관하게 Agent가 정책을 조회하는 인터페이스이다."""

    def get_response_policy(
        self,
        *,
        fraud_type: str,
        risk_grade: str,
    ) -> ResponsePolicy:
        """사기 유형과 위험등급에 정확히 일치하는 정책을 반환한다."""


__all__ = [
    "HIGH",
    "LOW",
    "MEDIUM",
    "RISK_GRADE_CODES",
    "VERY_HIGH",
    "PolicyAction",
    "PolicyChecklistItem",
    "PolicyError",
    "PolicyNotFoundError",
    "PolicyRepository",
    "PolicyValidationError",
    "ResponsePolicy",
]
