"""pyloader를 써서 적당히 임베딩"""
import os
import sys
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pypdf import PdfReader
from sqlmodel import Session, select

from app.core.db import engine
from app.data.model.cs_guide_document import CsGuideDocument
from app.data.model.cs_guide_document_chunk import CsGuideDocumentChunk

load_dotenv()

# 1. 임베딩 모델 설정 (db 연결 정보는 app.core.db 가 DATABASE_URL 로 관리한다)
embedder = OpenAIEmbeddings(model="text-embedding-3-small")

def query_embedding(text: str)->list[float]:
    """사용자 질문에 대한 임베딩 (문서 임베딩이랑 구분해서 쓰기)"""
    return embedder.embed_query(text)

def docs_embedding(texts: list[str])->list[list[float]]:
    """문서에 대한 임베딩"""
    return embedder.embed_documents(texts)

def normalize_page_text(raw_text: str | None) -> str:
    """PDF 에서 뽑은 페이지 텍스트를 저장 가능한 형태로 정리한다.

    - NUL(0x00) 제거: 일부 PDF 는 글리프 매핑 실패분을 0x00 으로 내보내는데,
      PostgreSQL 의 text 컬럼은 NUL 을 저장하지 못해 적재가 통째로 실패한다
      (psycopg DataError). 공백으로 바꿔 앞뒤 단어가 붙지 않게 한다.
    - 반복되는 공백·줄바꿈을 한 칸으로 접는다.
    """

    if not raw_text:
        return ""
    return " ".join(raw_text.replace("\x00", " ").split())


def get_chunks_from_pdf(pdf_path, overlap_size=100) -> tuple[str, str, list[dict]]:
    """
    페이지 단위로 청킹
    - 불량 페이지 전처리 (빈 청크는 무시 + 공백,줄바꿈 제거)
    - 이전 페이지 오버랩 기능 (overlap_size)

    리턴값: (pdf 제목, 전체 원문, 청킹된 리스트)
    """
    # 페이지 단위 텍스트 로드 (1페이지 = 1청크)
    reader = PdfReader(pdf_path)
    chunks = []
    full_text_parts = [] # 전체 원문 리턴용
    previous_context = "" # 앞 페이지 뒷부분을 기억할 변수

    for idx,page in enumerate(reader.pages):
        page_num = idx + 1
        cleaned_text = normalize_page_text(page.extract_text())

        # 텍스트가 실제로 있는 페이지만 청크로 만들겠다
        if not cleaned_text:
            continue
        full_text_parts.append(cleaned_text)

        # 앞 페이지의 오버랩 텍스트와 현재 페이지 텍스트 결합 (문맥 보존)
        if previous_context:
            chunk_content = f"...{previous_context} {cleaned_text}"
        else:
            chunk_content = cleaned_text

        chunks.append({
            "page": page_num,
            "content": chunk_content
        })
        # 다음 페이지를 위해 현재 페이지의 마지막 부분을 오버랩 사이즈만큼 보관
        previous_context = cleaned_text[-overlap_size:] if len(cleaned_text) > overlap_size else cleaned_text

    title = os.path.basename(pdf_path)
    full_text = "\n".join(full_text_parts)

    return title, full_text, chunks

def delete_document(title: str) -> bool:
    """같은 파일명으로 적재된 문서를 지운다. 청크는 FK CASCADE 로 함께 지워진다."""

    with Session(engine) as session:
        document = session.exec(
            select(CsGuideDocument).where(CsGuideDocument.title == title)
        ).first()
        if document is None:
            return False
        session.delete(document)
        session.commit()
        return True


def save_pdf(pdf_path):
    """pdf 를 청킹하여 임베딩한다"""

    title, full_text, chunks = get_chunks_from_pdf(pdf_path, overlap_size=100)

    contents = [chunk["content"] for chunk in chunks]
    vectors = docs_embedding(contents)

    # 디비 세션 확보 (engine 풀에서 커넥션을 빌려오고, 블록을 나가면 풀로 반환된다)
    with Session(engine) as session:
        # cs_guide_documents 테이블에 원본 문서 1건 먼저 넣고 DB 가 만든 id 를 받아온다
        document = CsGuideDocument(
            title=title,
            source=pdf_path,
            content=full_text,
        )
        session.add(document)
        # flush 는 INSERT 만 보내고 커밋은 하지 않는다. id 를 얻으려고 호출한다
        session.flush()

        # db 에 삽입 할 행 뭉텅이 만들기 (cs_guide_document_chunks)
        # (cs_guide_document_id, chunk_index) 가 UNIQUE 라 한 페이지에서 청크가
        # 여러 개 나와도 겹치지 않도록 문서 전체에서 단조 증가하는 순번을 쓰고,
        # 원본 페이지 번호는 page 컬럼에 따로 넣는다 (한 페이지에서 청크가 여러
        # 개 나오면 같은 page 값이 반복된다)
        document_chunks = []
        for chunk_index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            document_chunks.append(
                CsGuideDocumentChunk(
                    cs_guide_document_id=document.id,
                    chunk_index=chunk_index,
                    page=chunk["page"],
                    content=chunk["content"],
                    embedding=vector,
                )
            )

        # 문서에 대한 청크 내용 db에 저장하기 (INSERT 한 방으로 묶여서 나간다)
        session.add_all(document_chunks)

        # 문서와 청크가 같은 트랜잭션이라 하나라도 실패하면 통째로 롤백된다
        session.commit()

def main():
    """디렉터리의 모든 pdf 를 읽어서 임베딩한다.

    같은 파일명이 이미 적재돼 있으면 지우고 다시 넣는다. save_pdf 는 upsert 가
    아니라 매번 INSERT 이고 title 에 유니크 제약이 없어서, 그냥 두 번 돌리면
    문서와 청크가 중복 적재되고 검색이 같은 청크를 여러 번 물어오기 때문이다.

    적재 결과를 디렉터리와 일치시키려면 디렉터리에 넣을 pdf 만 두고 돌리면 된다.
    코퍼스에서 뺀 문서는 여기서 지워주지 않으므로 DB 에서 직접 지워야 한다.

        uv run --env-file .env python -m app.services.rag.docs_embedding
        uv run --env-file .env python -m app.services.rag.docs_embedding /다른/경로
    """
    # 인자로 경로를 주지 않으면 docs/embed_target_pdfs 를 쓴다
    if len(sys.argv) > 1:
        target_dir = os.path.abspath(sys.argv[1])
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        target_dir = os.path.normpath(
            os.path.join(base_dir, "..", "..", "..", "docs", "embed_target_pdfs")
        )

    if not os.path.isdir(target_dir):
        print(f"디렉터리가 아닙니다: {target_dir}")
        return

    # 경로에서 pdf 각각의 경로명 읽어오기
    pdf_paths = sorted(
        os.path.join(target_dir, filename)
        for filename in os.listdir(target_dir)
        if filename.lower().endswith(".pdf")
    )

    if not pdf_paths:
        print(f"임베딩할 pdf가 없습니다: {target_dir}")
        return

    print(f"{target_dir} (pdf {len(pdf_paths)}개)")
    saved, skipped, failed = 0, [], []

    for pdf_path in pdf_paths:
        title = os.path.basename(pdf_path)

        # try except 로 감싸줘야 중간에 에러나도 다른 파일들은 전부 임베딩 할 수 있다
        try:
            # 텍스트가 없는 이미지형 pdf 는 청크가 0개라 적재할 것이 없다
            _, _, chunks = get_chunks_from_pdf(pdf_path)
            if not chunks:
                skipped.append(title)
                print(f"건너뜀(텍스트 없음): {title}")
                continue

            if delete_document(title):
                print(f"기존 적재분 삭제: {title}")
            save_pdf(pdf_path)
        except Exception as e:
            failed.append((title, e))
            print(f"임베딩 실패: {title} - {e}")
            continue

        saved += 1
        print(f"임베딩 완료: {title} ({len(chunks)}청크)")

    print(f"\n적재 {saved} / 건너뜀 {len(skipped)} / 실패 {len(failed)}")


if __name__ == "__main__":
    main()