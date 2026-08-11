"""Markdown 대응 가이드를 검증하고 의미 단위 검색 청크로 변환한다."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.domain.agent_guide import (
    DuplicateGuideDocumentError,
    GuideChunk,
    GuideChunkingError,
    GuideCorpusError,
    GuideDocument,
    GuideDocumentParseError,
    GuideDocumentValidationError,
)
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[3] / "docs" / "agent_guides"
)
DOCUMENT_DIRECTORY_NAMES = ("official", "internal_demo")

OFFICIAL_GUIDE = "OFFICIAL_GUIDE"
INTERNAL_DEMO_GUIDE = "INTERNAL_DEMO_GUIDE"
ALLOWED_SOURCE_TYPES = frozenset({OFFICIAL_GUIDE, INTERNAL_DEMO_GUIDE})
ALLOWED_AUDIENCES = frozenset({"MONITORING", "CUSTOMER", "COMMON"})
ALLOWED_TOPICS = frozenset(
    {
        "CUSTOMER_CONFIRMATION",
        "SECURITY_CHECK",
        "RECIPIENT_ACCOUNT_REVIEW",
        "ADDITIONAL_TRANSACTION_REVIEW",
        "ACCOUNT_FLOW_REVIEW",
        "DAMAGE_REPORT",
        "MESSENGER_IDENTITY_CHECK",
        "EMERGENCY_RESPONSE",
        "MANUAL_REVIEW",
    }
)
OFFICIAL_SOURCE_HOSTS = frozenset(
    {
        "www.fss.or.kr",
        "www.fsec.or.kr",
        "www.fsc.go.kr",
        "ecrm.police.go.kr",
    }
)
INTERNAL_DEMO_SOURCE_NAME = "FDShield 시연용 내부 지침"

_FRONT_MATTER_DELIMITER = "---"
_MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


class _GuideMetadataSchema(BaseModel):
    """문서 코퍼스 계약을 엄격하게 검증하는 내부 입력 스키마이다."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    document_id: NonEmptyText
    title: NonEmptyText
    source_type: NonEmptyText
    source_name: NonEmptyText
    source_url: NonEmptyText | None
    fraud_types: tuple[NonEmptyText, ...] = Field(min_length=1)
    audiences: tuple[NonEmptyText, ...] = Field(min_length=1)
    topics: tuple[NonEmptyText, ...] = Field(min_length=1)
    published_at: date | None
    accessed_at: date

    @model_validator(mode="after")
    def validate_metadata_contract(self) -> Self:
        """코드 허용 범위와 공식·내부 출처 규칙을 함께 검증한다."""

        if self.source_type not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"지원하지 않는 문서 출처 유형이다: {self.source_type}")
        if not set(self.fraud_types).issubset(FINAL_FRAUD_TYPE_CODES):
            raise ValueError("지원하지 않는 사기 유형 코드가 포함되어 있다.")
        if not set(self.audiences).issubset(ALLOWED_AUDIENCES):
            raise ValueError("지원하지 않는 문서 대상 코드가 포함되어 있다.")
        if not set(self.topics).issubset(ALLOWED_TOPICS):
            raise ValueError("지원하지 않는 대응 주제 코드가 포함되어 있다.")

        if self.source_type == OFFICIAL_GUIDE:
            if self.source_url is None:
                raise ValueError("공식 문서에는 HTTPS 원문 URL이 필요하다.")
            parsed_url = urlparse(self.source_url)
            if parsed_url.scheme != "https" or parsed_url.hostname not in OFFICIAL_SOURCE_HOSTS:
                raise ValueError("공식 문서 URL이 허용된 기관의 HTTPS 주소가 아니다.")
        elif self.source_url is not None or self.source_name != INTERNAL_DEMO_SOURCE_NAME:
            raise ValueError("시연용 내부 문서의 출처 메타데이터가 올바르지 않다.")

        return self


def discover_guide_paths(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
) -> tuple[Path, ...]:
    """공식·내부 디렉터리의 Markdown 문서를 재현 가능한 순서로 찾는다."""

    root = Path(corpus_root)
    paths: list[Path] = []
    for directory_name in DOCUMENT_DIRECTORY_NAMES:
        document_root = root / directory_name
        if not document_root.is_dir():
            raise GuideCorpusError(f"대응 가이드 디렉터리가 없다: {document_root}")
        paths.extend(
            path
            for path in document_root.glob("*.md")
            if path.name.casefold() != "readme.md"
        )

    if not paths:
        raise GuideCorpusError(f"검색 대상 대응 가이드 문서가 없다: {root}")

    # 운영체제의 파일 열거 순서와 무관하게 항상 같은 결과를 만들기 위한 정렬이다.
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def load_guide_document(path: str | Path) -> GuideDocument:
    """한 Markdown 파일의 Front Matter와 본문을 검증해 도메인 객체로 만든다."""

    document_path = Path(path)
    try:
        raw_text = document_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GuideDocumentParseError(
            f"대응 가이드 파일을 읽을 수 없다: {document_path}"
        ) from error

    raw_metadata, body = _split_front_matter(raw_text, document_path)
    try:
        parsed_metadata = yaml.safe_load(raw_metadata)
    except yaml.YAMLError as error:
        raise GuideDocumentParseError(
            f"Front Matter YAML 형식이 올바르지 않다: {document_path}"
        ) from error

    try:
        metadata = _GuideMetadataSchema.model_validate(parsed_metadata)
    except ValidationError as error:
        raise GuideDocumentValidationError(
            f"대응 가이드 메타데이터가 올바르지 않다: {document_path}: {error}"
        ) from error

    normalized_body = body.strip()
    if not normalized_body:
        raise GuideDocumentValidationError(
            f"대응 가이드 본문이 비어 있다: {document_path}"
        )

    return GuideDocument(
        document_id=metadata.document_id,
        title=metadata.title,
        source_type=metadata.source_type,
        source_name=metadata.source_name,
        source_url=metadata.source_url,
        fraud_types=metadata.fraud_types,
        audiences=metadata.audiences,
        topics=metadata.topics,
        published_at=metadata.published_at,
        accessed_at=metadata.accessed_at,
        content=normalized_body,
        source_path=document_path.resolve(),
    )


def load_guide_corpus(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
) -> tuple[GuideDocument, ...]:
    """전체 코퍼스를 로딩하고 문서 ID 중복까지 검증한다."""

    documents = tuple(
        load_guide_document(path) for path in discover_guide_paths(corpus_root)
    )
    seen_paths: dict[str, Path] = {}
    for document in documents:
        expected_source_type = (
            OFFICIAL_GUIDE
            if document.source_path.parent.name == "official"
            else INTERNAL_DEMO_GUIDE
        )
        if document.source_type != expected_source_type:
            raise GuideDocumentValidationError(
                "문서 디렉터리와 source_type이 일치하지 않는다: "
                f"{document.source_path}: {document.source_type}"
            )

        previous_path = seen_paths.get(document.document_id)
        if previous_path is not None:
            raise DuplicateGuideDocumentError(
                "중복된 document_id가 있다: "
                f"{document.document_id}: {previous_path}, {document.source_path}"
            )
        seen_paths[document.document_id] = document.source_path

    return documents


def create_guide_chunks(document: GuideDocument) -> tuple[GuideChunk, ...]:
    """문서의 H1 소개와 H2 섹션을 문맥이 보존된 검색 청크로 만든다."""

    raw_sections = _split_semantic_sections(document)
    chunks = tuple(
        GuideChunk(
            document_id=document.document_id,
            document_title=document.title,
            chunk_index=index,
            heading=heading,
            content=content,
            source_type=document.source_type,
            source_name=document.source_name,
            source_url=document.source_url,
            fraud_types=document.fraud_types,
            audiences=document.audiences,
            topics=document.topics,
        )
        for index, (heading, content) in enumerate(raw_sections)
    )
    if not chunks:
        raise GuideChunkingError(
            f"검색 가능한 문서 청크가 없다: {document.source_path}"
        )
    return chunks


def load_and_chunk_guide_corpus(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
) -> tuple[GuideChunk, ...]:
    """검증된 전체 문서를 로딩하고 문서 순서대로 청크를 반환한다."""

    return tuple(
        chunk
        for document in load_guide_corpus(corpus_root)
        for chunk in create_guide_chunks(document)
    )


def _split_front_matter(raw_text: str, path: Path) -> tuple[str, str]:
    """줄바꿈 형식에 영향받지 않고 YAML Front Matter와 본문을 나눈다."""

    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        raise GuideDocumentParseError(
            f"Front Matter 시작 구분자가 없다: {path}"
        )

    try:
        end_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == _FRONT_MATTER_DELIMITER
        )
    except StopIteration as error:
        raise GuideDocumentParseError(
            f"Front Matter 종료 구분자가 없다: {path}"
        ) from error

    return "\n".join(lines[1:end_index]), "\n".join(lines[end_index + 1 :])


def _split_semantic_sections(document: GuideDocument) -> tuple[tuple[str, str], ...]:
    """H2에서만 분리하고 H3 이하 제목은 상위 섹션 문맥에 남겨 둔다."""

    sections: list[tuple[str, str]] = []
    current_heading = document.title
    current_level = 1
    current_lines: list[str] = []

    def flush_section() -> None:
        body = "\n".join(current_lines).strip()
        if not body:
            return
        heading_prefix = "#" if current_level == 1 else "##"
        content = f"{heading_prefix} {current_heading}\n\n{body}"
        sections.append((current_heading, content))

    for line in document.content.splitlines():
        heading_match = _MARKDOWN_HEADING_PATTERN.match(line)
        if heading_match and len(heading_match.group(1)) <= 2:
            flush_section()
            current_level = len(heading_match.group(1))
            current_heading = heading_match.group(2).strip()
            current_lines = []
            continue
        current_lines.append(line)

    flush_section()
    return tuple(sections)


__all__ = [
    "ALLOWED_AUDIENCES",
    "ALLOWED_SOURCE_TYPES",
    "ALLOWED_TOPICS",
    "DEFAULT_CORPUS_ROOT",
    "DOCUMENT_DIRECTORY_NAMES",
    "INTERNAL_DEMO_GUIDE",
    "INTERNAL_DEMO_SOURCE_NAME",
    "OFFICIAL_GUIDE",
    "OFFICIAL_SOURCE_HOSTS",
    "create_guide_chunks",
    "discover_guide_paths",
    "load_and_chunk_guide_corpus",
    "load_guide_corpus",
    "load_guide_document",
]
