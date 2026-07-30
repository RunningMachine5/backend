from dataclasses import dataclass

from app.domain.enums import AgentAction, FraudType, RiskGrade


@dataclass(frozen=True)
class RagQueryDTO:
    """위험등급, 사기유형, 근거를 조합한 RAG 질의 DTO."""

    risk_grade: RiskGrade
    fraud_type: FraudType
    evidence: list[str]
    query: str


@dataclass(frozen=True)
class RetrievedContextDTO:
    """Fake VectorDB가 반환한 모니터링 담당자용 문맥 DTO."""

    title: str
    content: str


@dataclass(frozen=True)
class AgentResultDTO:
    """위험등급 분기 이후 Agent의 최종 작업 결과 DTO."""

    action: AgentAction
    message: str

