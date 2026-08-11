"""실제 Agent 전환 전까지 기존 Fake 파이프라인이 사용하는 DTO."""

from dataclasses import dataclass

from app.domain.enums import AgentAction, FraudType, RiskGrade


@dataclass(frozen=True)
class RagQueryDTO:
    """위험등급, 사기유형, 근거를 조합한 기존 RAG 질의 DTO."""

    risk_grade: RiskGrade
    fraud_type: FraudType
    evidence: list[str]
    query: str


@dataclass(frozen=True)
class RetrievedContextDTO:
    """기존 Fake VectorDB가 반환하는 문맥 DTO."""

    title: str
    content: str
    source: str = ""
    similarity_score: float = 0.0


@dataclass(frozen=True)
class AgentResultDTO:
    """기존 모니터링 Agent 스켈레톤의 실행 결과 DTO."""

    action: AgentAction
    message: str
    rag_query: RagQueryDTO | None = None
    retrieved_context: RetrievedContextDTO | None = None


__all__ = ["AgentResultDTO", "RagQueryDTO", "RetrievedContextDTO"]
