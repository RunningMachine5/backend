from dataclasses import dataclass

@dataclass(frozen=True)
class ChatbotRequestDTO:
    """고객 식별자와 질문을 전달하는 챗봇 요청 DTO."""
    question: str

@dataclass(frozen=True)
class CustomerGuideDTO:
    """고객 대응 가이드 검색 결과 DTO."""

    title: str
    content: str
    source: str
    similarity_score: float


@dataclass(frozen=True)
class ChatbotResponseDTO:
    """검색 가이드와 거래정보를 반영한 챗봇 응답 DTO."""

    answer: str
    source: list[str]
