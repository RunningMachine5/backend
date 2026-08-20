"""RAG 평가 골든셋(evals/rag/datasets/golden_v1.jsonl)을 읽어 들인다.

JSONL 한 줄이 평가 사례 하나다. 파일에는 RAGAS가 쓰지 않는 메타데이터
(category / difficulty / 문서·페이지 / required_claims)까지 담고, 이 모듈이
RAGAS가 요구하는 필드(user_input / reference / reference_contexts)로 변환한다.
슬라이스 리포트가 메타데이터를 써야 하므로 JSONL을 평탄하게 만들지 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# 답변 가능 사례의 근거는 항상 이 다섯 카테고리 중 하나에 속한다.
ANSWERABLE_CATEGORIES = ("easy", "hard", "multi_doc", "page_boundary")
CATEGORIES = (*ANSWERABLE_CATEGORIES, "unanswerable")

# 대응 가이드가 담긴 페이지와, 상황·사례만 있는 앞 페이지를 구분한다.
GUIDE_ROLE = "guide"
BEHAVIOR_ROLE = "behavior"

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[3] / "evals" / "rag" / "datasets" / "golden_v1.jsonl"
)


def golden_dataset_path() -> Path:
    """골든셋 경로. RAG_GOLDEN_DATASET_PATH 로 덮어쓸 수 있다."""

    override = os.getenv("RAG_GOLDEN_DATASET_PATH", "").strip()
    return Path(override) if override else _DEFAULT_PATH


@dataclass(frozen=True, slots=True)
class GoldenContext:
    """근거 한 조각. evidence 는 해당 PDF 페이지 원문의 부분 문자열이다."""

    document_id: str
    filename: str
    page: int
    context_role: str
    evidence: str

    @property
    def locator(self) -> tuple[str, int]:
        """검색 결과와 대조할 (문서 파일명, 페이지) 키."""

        return (self.filename, self.page)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """평가 사례 하나."""

    id: str
    category: str
    difficulty: str
    user_input: str
    expected_behavior: str
    reference: str
    reference_contexts: tuple[GoldenContext, ...]
    required_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    tags: tuple[str, ...]
    review_status: str

    @property
    def is_abstain(self) -> bool:
        return self.expected_behavior == "ABSTAIN"

    def locators(self) -> set[tuple[str, int]]:
        """근거의 (파일명, 페이지) 집합. 리포트에서 실제 검색 결과와 눈으로 대조한다."""

        return {context.locator for context in self.reference_contexts}

    def to_ragas_fields(self) -> dict[str, object]:
        """RAGAS SingleTurnSample 이 요구하는 필드만 뽑는다."""

        return {
            "user_input": self.user_input,
            "reference": self.reference,
            "reference_contexts": [c.evidence for c in self.reference_contexts],
        }


def load_golden_cases(path: Path | str | None = None) -> tuple[GoldenCase, ...]:
    """골든셋 JSONL 을 읽어 GoldenCase 튜플로 돌려준다."""

    target = Path(path) if path is not None else golden_dataset_path()
    cases = []
    with target.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{target}:{line_number} JSON 파싱 실패") from exc
            cases.append(_build_case(raw))
    return tuple(cases)


def _build_case(raw: dict) -> GoldenCase:
    return GoldenCase(
        id=raw["id"],
        category=raw["category"],
        difficulty=raw["difficulty"],
        user_input=raw["user_input"],
        expected_behavior=raw["expected_behavior"],
        reference=raw["reference"],
        reference_contexts=tuple(
            GoldenContext(
                document_id=c["document_id"],
                filename=c["filename"],
                page=c["page"],
                context_role=c["context_role"],
                evidence=c["evidence"],
            )
            for c in raw["reference_contexts"]
        ),
        required_claims=tuple(raw["required_claims"]),
        forbidden_claims=tuple(raw["forbidden_claims"]),
        tags=tuple(raw["tags"]),
        review_status=raw["review_status"],
    )


__all__ = [
    "ANSWERABLE_CATEGORIES",
    "BEHAVIOR_ROLE",
    "CATEGORIES",
    "GUIDE_ROLE",
    "GoldenCase",
    "GoldenContext",
    "golden_dataset_path",
    "load_golden_cases",
]
