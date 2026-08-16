"""내부 대응 정책과 RAG 문서의 조치별 커버리지를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.domain.agent_guide import GuideDocument
from app.domain.response_policy import ResponsePolicy


COVERED = "COVERED"
MISSING = "MISSING"
COMMON_AUDIENCE = "COMMON"


@dataclass(frozen=True, slots=True)
class GuideCoverageRow:
    """정책 조치 하나와 이를 지원하는 대응 문서의 연결 결과이다."""

    fraud_type: str
    risk_grade: str
    audience: str
    action_code: str
    matching_document_count: int
    matching_document_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class GuideCoverageReport:
    """전체 정책 조치의 대응 문서 커버리지 요약이다."""

    rows: tuple[GuideCoverageRow, ...]
    broad_document_ids: tuple[str, ...]

    @property
    def covered_count(self) -> int:
        return sum(row.status == COVERED for row in self.rows)

    @property
    def missing_count(self) -> int:
        return sum(row.status == MISSING for row in self.rows)

    @property
    def coverage_rate(self) -> float:
        if not self.rows:
            return 0.0
        return self.covered_count / len(self.rows)


def analyze_guide_coverage(
    policies: Sequence[ResponsePolicy],
    documents: Sequence[GuideDocument],
    *,
    audience: str = "MONITORING",
) -> GuideCoverageReport:
    """실제 정책에 존재하는 조치만 문서 검색 조건과 동일하게 비교한다."""

    rows: list[GuideCoverageRow] = []
    for policy in policies:
        for action in policy.actions:
            matching_ids = tuple(
                document.document_id
                for document in documents
                if policy.fraud_type in document.fraud_types
                and policy.risk_grade in document.risk_grades
                and (
                    audience in document.audiences
                    or COMMON_AUDIENCE in document.audiences
                )
                and action.action_code in document.action_codes
            )
            rows.append(
                GuideCoverageRow(
                    fraud_type=policy.fraud_type,
                    risk_grade=policy.risk_grade,
                    audience=audience,
                    action_code=action.action_code,
                    matching_document_count=len(matching_ids),
                    matching_document_ids=matching_ids,
                    status=COVERED if matching_ids else MISSING,
                )
            )

    # 여러 유형을 지원하는 공통 문서는 오류로 처리하지 않고 검토 대상으로만 표시한다.
    broad_document_ids = tuple(
        document.document_id
        for document in documents
        if len(document.fraud_types) >= 3
    )
    return GuideCoverageReport(
        rows=tuple(rows),
        broad_document_ids=broad_document_ids,
    )


__all__ = [
    "COVERED",
    "MISSING",
    "GuideCoverageReport",
    "GuideCoverageRow",
    "analyze_guide_coverage",
]
