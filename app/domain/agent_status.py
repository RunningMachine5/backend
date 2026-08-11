"""AI Agent 입출력과 실행 흐름에서 공통으로 사용하는 상태 코드."""

from enum import Enum


class AgentExecutionStatus(str, Enum):
    """Agent 사건의 실행 상태."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RuleFilterStatus(str, Enum):
    """Rule Engine의 유형 분류 실행 상태."""

    APPLIED = "APPLIED"
    SKIPPED_NOT_FRAUD = "SKIPPED_NOT_FRAUD"
    FAILED = "FAILED"


class ClassificationStatus(str, Enum):
    """Rule 상위 유형 점수의 확실성 상태."""

    CONFIDENT = "CONFIDENT"
    AMBIGUOUS = "AMBIGUOUS"


class InvestigationStatus(str, Enum):
    """애매한 유형에 대한 Agent 조사 상태."""

    NOT_REQUIRED = "NOT_REQUIRED"
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    FAILED = "FAILED"


class InformationStatus(str, Enum):
    """대응 계획 생성에 필요한 정보의 충분성."""

    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


__all__ = [
    "AgentExecutionStatus",
    "ClassificationStatus",
    "InformationStatus",
    "InvestigationStatus",
    "RuleFilterStatus",
]
