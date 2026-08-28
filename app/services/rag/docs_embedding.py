"""고객 대응 가이드 PDF를 청킹하고 임베딩해 원자적으로 교체한다."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import delete
from sqlmodel import Session

from app.core.db import engine
from app.data.model.cs_guide_document import CsGuideDocument
from app.data.model.cs_guide_document_chunk import CsGuideDocumentChunk

load_dotenv()

# 평가에서 채택한 U-1200 설정. 정상적인 제목 경계에는 overlap을 추가하지 않고,
# hard max를 넘은 요소를 분할할 때만 100자를 겹친다.
MAX_CHARACTERS = 1200
NEW_AFTER_N_CHARS = 800
COMBINE_TEXT_UNDER_N_CHARS = 200
OVERLAP = 100

# DB 연결 정보는 app.core.db 가 DATABASE_URL 로 관리한다.
embedder = OpenAIEmbeddings(model="text-embedding-3-small")


class EmptyPdfError(ValueError):
    """Unstructured ``fast``에서 저장할 텍스트를 얻지 못했다."""


class EmbeddingCountMismatchError(ValueError):
    """청크 수와 임베딩 수가 달라 안전하게 교체할 수 없다."""


def query_embedding(text: str) -> list[float]:
    """사용자 질문에 대한 임베딩을 생성한다."""

    return embedder.embed_query(text)


def docs_embedding(texts: list[str]) -> list[list[float]]:
    """문서 청크에 대한 임베딩을 생성한다."""

    return embedder.embed_documents(texts)


def normalize_page_text(raw_text: str | None) -> str:
    """PDF 텍스트에서 PostgreSQL이 저장할 수 없는 NUL과 빈 공백을 정리한다."""

    if not raw_text:
        return ""
    return " ".join(raw_text.replace("\x00", " ").split())


def get_chunks_from_unstructured_pdf(
    pdf_path: str | os.PathLike[str],
    *,
    partitioner: Callable[..., Sequence[Any]] | None = None,
    chunker: Callable[..., Sequence[Any]] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """
    unstructured의 fast 전략으로 partition 후 제목·페이지 경계를 보존해 의미 단위 청크를 만든다.
    ``partitioner``와 ``chunker``는 단위 테스트용 입력 부분 실 사용시에는 무시해도됨
    """

    using_default_partitioner = partitioner is None
    if using_default_partitioner and not pdf_has_extractable_text(pdf_path):
        return Path(pdf_path).name, "", []

    if partitioner is None or chunker is None:
        try:
            from unstructured.chunking.title import chunk_by_title
            from unstructured.partition.pdf import partition_pdf
        except ImportError as exc:
            raise RuntimeError(
                "Unstructured 인덱싱 의존성이 없습니다. "
                "`uv sync --group indexing`으로 설치하세요."
            ) from exc
        partitioner = partitioner or partition_pdf
        chunker = chunker or chunk_by_title

    elements = partitioner(
        filename=os.fspath(pdf_path),
        strategy="fast",
        languages=["kor", "eng"],
    )
    cleaned_elements = []
    full_text_parts = []
    for element in elements:
        text = normalize_page_text(getattr(element, "text", str(element)))
        if not text:
            continue
        element.text = text
        cleaned_elements.append(element)
        full_text_parts.append(text)

    if not cleaned_elements:
        return Path(pdf_path).name, "", []

    chunk_elements = chunker(
        cleaned_elements,
        max_characters=MAX_CHARACTERS,
        new_after_n_chars=NEW_AFTER_N_CHARS,
        combine_text_under_n_chars=COMBINE_TEXT_UNDER_N_CHARS,
        overlap=OVERLAP,
        overlap_all=False,
        multipage_sections=False,
    )

    chunks = []
    for chunk in chunk_elements:
        content = normalize_page_text(getattr(chunk, "text", str(chunk)))
        if not content:
            continue
        chunks.append({"page": _chunk_page_number(chunk), "content": content})

    return Path(pdf_path).name, "\n".join(full_text_parts), chunks


def pdf_has_extractable_text(pdf_path: str | os.PathLike[str]) -> bool:
    """pdf에 텍스트가 하나라도 있는지 확인한다 (이미지만 있으면 추출하지 않는다)
    """

    from pypdf import PdfReader

    return any(
        normalize_page_text(page.extract_text())
        for page in PdfReader(pdf_path).pages
    )


def _chunk_page_number(chunk: Any) -> int:
    """페이지 경계를 보존한 Unstructured 청크에서 1부터 시작하는 페이지를 얻는다."""

    metadata = getattr(chunk, "metadata", None)
    page_number = getattr(metadata, "page_number", None)
    if page_number is not None:
        return int(page_number)

    original_elements = getattr(metadata, "orig_elements", None) or ()
    pages = {
        int(page)
        for element in original_elements
        if (page := getattr(getattr(element, "metadata", None), "page_number", None))
        is not None
    }
    if len(pages) == 1:
        return pages.pop()
    if not pages:
        raise ValueError("Unstructured 청크에 페이지 번호가 없습니다.")
    raise ValueError(f"페이지 경계를 넘는 Unstructured 청크입니다: {sorted(pages)}")


def prepare_pdf(
    pdf_path: str | os.PathLike[str],
    *,
    embedding_fn: Callable[[list[str]], list[list[float]]] = docs_embedding,
) -> tuple[str, str, list[dict[str, Any]], list[list[float]]]:
    """DB를 건드리기 전에 문서 하나의 파싱과 전체 임베딩을 완료한다."""

    title, full_text, chunks = get_chunks_from_unstructured_pdf(pdf_path)

    if not chunks:
        raise EmptyPdfError(f"텍스트 청크가 없습니다: {title}")

    vectors = embedding_fn([chunk["content"] for chunk in chunks])
    if len(vectors) != len(chunks):
        raise EmbeddingCountMismatchError(
            f"청크 {len(chunks)}개와 임베딩 {len(vectors)}개의 개수가 다릅니다: {title}"
        )
    return title, full_text, chunks, vectors


def replace_document(
    session: Session,
    *,
    title: str,
    source: str,
    content: str,
    chunks: Sequence[dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> None:
    """같은 제목의 기존 적재분을 현재 트랜잭션 안에서 신규 데이터로 교체한다."""

    if len(chunks) != len(vectors):
        raise EmbeddingCountMismatchError(
            f"청크 {len(chunks)}개와 임베딩 {len(vectors)}개의 개수가 다릅니다: {title}"
        )

    # DELETE를 즉시 실행해 같은 제목으로 중복 적재된 과거 행까지 전부 정리한다.
    # 이후 INSERT가 실패하면 바깥 transaction이 DELETE도 함께 롤백한다.
    session.exec(delete(CsGuideDocument).where(CsGuideDocument.title == title))

    document = CsGuideDocument(title=title, source=source, content=content)
    session.add(document)
    session.flush()
    if document.id is None:
        raise RuntimeError(f"문서 ID를 발급받지 못했습니다: {title}")

    session.add_all(
        [
            CsGuideDocumentChunk(
                cs_guide_document_id=document.id,
                chunk_index=chunk_index,
                page=int(chunk["page"]),
                content=str(chunk["content"]),
                embedding=list(vector),
            )
            for chunk_index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
    )


def save_pdf(
    pdf_path: str | os.PathLike[str],
) -> int:
    """문서 하나를 준비한 뒤 단일 DB 트랜잭션으로 기존 적재분과 교체한다."""

    title, full_text, chunks, vectors = prepare_pdf(pdf_path)

    with Session(engine) as session:
        with session.begin():
            replace_document(
                session,
                title=title,
                source=os.fspath(pdf_path),
                content=full_text,
                chunks=chunks,
                vectors=vectors,
            )
    return len(chunks)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="고객 대응 가이드 PDF를 문서별 원자적 트랜잭션으로 인덱싱"
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=str(Path(__file__).resolve().parents[3] / "docs" / "embed_target_pdfs"),
        help="PDF 디렉터리 (기본값: docs/embed_target_pdfs)",
    )
    return parser.parse_args(argv)


# PDF 적제 테스트용
def main(argv: Sequence[str] | None = None) -> int:
    """디렉터리의 PDF를 각각 독립된 트랜잭션으로 인덱싱한다."""

    args = _parse_args(argv)
    target_dir = Path(args.target_dir).expanduser().resolve()
    if not target_dir.is_dir():
        print(f"디렉터리가 아닙니다: {target_dir}")
        return 2

    pdf_paths = sorted(target_dir.glob("*.pdf"), key=lambda path: path.name.lower())
    if not pdf_paths:
        print(f"임베딩할 pdf가 없습니다: {target_dir}")
        return 2

    print(f"{target_dir} (pdf {len(pdf_paths)}개, 청킹 U-1200)")

    saved = 0
    failed: list[tuple[str, Exception]] = []
    for pdf_path in pdf_paths:
        try:
            chunk_count = save_pdf(pdf_path)
        except Exception as exc:
            failed.append((pdf_path.name, exc))
            label = "인덱싱 제외" if isinstance(exc, EmptyPdfError) else "인덱싱 실패"
            print(f"{label}: {pdf_path.name} - {exc}")
            continue

        saved += 1
        print(f"임베딩 완료: {pdf_path.name} ({chunk_count}청크)")

    print(f"\n적재 {saved} / 실패·제외 {len(failed)}")
    if failed:
        print("실패 목록:")
        for title, exc in failed:
            print(f"- {title}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
