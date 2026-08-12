"""대응 가이드 검색 품질을 반복 측정하기 위한 평가 질의를 로딩한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.domain.agent_guide import GuideDocumentValidationError
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.services.agent.guide_corpus import (
    ALLOWED_AUDIENCES,
    ALLOWED_RISK_GRADES,
)


DEFAULT_GUIDE_EVALUATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "agent"
    / "guide_retrieval_evaluation.yaml"
)
NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class GuideRetrievalEvaluationCase:
    """하나의 검색 질문과 검색 결과에 포함되어야 하는 문서 계약이다."""

    query_id: str
    query: str
    fraud_type: str
    audience: str
    risk_grade: str
    action_codes: tuple[str, ...]
    expected_document_ids: tuple[str, ...]


class _EvaluationCaseSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    query_id: NonEmptyText
    query: NonEmptyText
    fraud_type: NonEmptyText
    audience: NonEmptyText
    risk_grade: NonEmptyText
    action_codes: tuple[NonEmptyText, ...] = Field(min_length=1)
    expected_document_ids: tuple[NonEmptyText, ...] = Field(min_length=1)


class _EvaluationDocumentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: tuple[_EvaluationCaseSchema, ...] = Field(min_length=1)


def load_guide_evaluation_cases(
    path: str | Path = DEFAULT_GUIDE_EVALUATION_PATH,
) -> tuple[GuideRetrievalEvaluationCase, ...]:
    """YAML 평가 세트를 검증하여 검색 평가에서 재사용할 불변 객체로 만든다."""

    evaluation_path = Path(path)
    try:
        raw_document = yaml.safe_load(evaluation_path.read_text(encoding="utf-8"))
        validated = _EvaluationDocumentSchema.model_validate(raw_document)
    except FileNotFoundError:
        raise
    except yaml.YAMLError as error:
        raise GuideDocumentValidationError(
            f"검색 평가 YAML 형식이 올바르지 않다: {evaluation_path}"
        ) from error
    except ValidationError as error:
        raise GuideDocumentValidationError(
            f"검색 평가 데이터가 올바르지 않다: {error}"
        ) from error

    cases = tuple(
        GuideRetrievalEvaluationCase(
            query_id=item.query_id,
            query=item.query,
            fraud_type=item.fraud_type,
            audience=item.audience,
            risk_grade=item.risk_grade,
            action_codes=item.action_codes,
            expected_document_ids=item.expected_document_ids,
        )
        for item in validated.cases
    )
    _validate_evaluation_contract(cases)
    return cases


def _validate_evaluation_contract(
    cases: tuple[GuideRetrievalEvaluationCase, ...],
) -> None:
    query_ids = [case.query_id for case in cases]
    if len(query_ids) != len(set(query_ids)):
        raise GuideDocumentValidationError("검색 평가 query_id가 중복되었다.")

    for case in cases:
        if case.fraud_type not in FINAL_FRAUD_TYPE_CODES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 사기 유형이 있다: {case.query_id}"
            )
        if case.audience not in ALLOWED_AUDIENCES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 대상 코드가 있다: {case.query_id}"
            )
        if case.risk_grade not in ALLOWED_RISK_GRADES:
            raise GuideDocumentValidationError(
                f"검색 평가에 지원하지 않는 위험등급이 있다: {case.query_id}"
            )


__all__ = [
    "DEFAULT_GUIDE_EVALUATION_PATH",
    "GuideRetrievalEvaluationCase",
    "load_guide_evaluation_cases",
]
