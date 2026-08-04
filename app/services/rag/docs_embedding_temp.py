"""pyloader를 써서 적당히 임베딩"""
import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pypdf import PdfReader

load_dotenv()

# 1. 임베딩 모델 및 데이터베이스 연결 설정
embedder = OpenAIEmbeddings(model="text-embedding-3-small")

# pgvector 연결 정보
DB_PARAMS = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "root"),
    "password": os.getenv("POSTGRES_PASSWORD", "1234"),
    "database": os.getenv("POSTGRES_DB", "edudb"),
}

def query_embedding(text: str)->list[float]:
    """사용자 질문에 대한 임베딩 (문서 임베딩이랑 구분해서 쓰기)"""
    return embedder.embed_query(text)

def docs_embedding(texts: list[str])->list[list[float]]:
    """문서에 대한 임베딩"""
    return embedder.embed_documents(texts)

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
        raw_text = page.extract_text()

        # 텍스트가 실제로 있는 페이지만 청크로 만들겠다
        if not raw_text.strip():
            continue

        # 반복되는 공백 및 줄바꿈 전처리
        cleaned_text = " ".join(raw_text.split())
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

def save_pdf(pdf_path):
    """pdf 를 청킹하여 임베딩한다"""

    title, full_text, chunks = get_chunks_from_pdf(pdf_path, overlap_size=100)

    # 디비 커넥션 확보
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    # 혹시 백터 확장이 안켜져 있으면 작동
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    contents = [chunk["content"] for chunk in chunks]
    vectors = docs_embedding(contents)

    # documents 테이블에 원본 문서 1건 먼저 삽입하고 생성된 id를 받아온다
    cur.execute(
        """
        INSERT INTO documents (title, source, content, created_at, updated_at)
        VALUES (%s, %s, %s, now(), now())
        RETURNING id
        """,
        (title, pdf_path, full_text)
    )
    # psycopg2에서 방금 실행한 쿼리 결과의 첫 번째 행을 가져오는 메서드
    document_id = cur.fetchone()[0]

    # db 에 삽입 할 행 뭉텅이 만들기 (document_chunks)
    data_to_insert=[]
    for chunk,vector in zip(chunks,vectors):
        col_document_id=document_id
        col_chunk_index=chunk["page"]
        col_content=chunk["content"]
        col_embedding=vector
        data_to_insert.append((
            col_document_id,
            col_chunk_index,
            col_content,
            col_embedding
        ))

    # 문서에 대한 청크 내용 db에 저장하기
    execute_values(
        cur,
        """
        INSERT INTO document_chunks (document_id, chunk_index, content, embedding, created_at)
        VALUES %s
        """,
        data_to_insert,
        template="(%s, %s, %s, %s, now())"
    )

    conn.commit()
    cur.close()
    conn.close()

def main():
    """
    docs/embed_target_pdfs 경로의 모든 pdf 를 읽어서 임베딩
    로컬 테스트용!
    """
    # 경로 정의
    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(base_dir, "..", "..", "..", "docs", "embed_target_pdfs")
    target_dir = os.path.normpath(target_dir)

    # 경로에서 pdf 각각의 경로명 읽어오기
    pdf_paths = []
    for filename in os.listdir(target_dir):
        if filename.lower().endswith(".pdf"):
            pdf_paths.append(os.path.join(target_dir, filename))
    pdf_paths = sorted(pdf_paths)

    if not pdf_paths:
        print(f"임베딩할 pdf가 없습니다: {target_dir}")
        return

    for pdf_path in pdf_paths:
        # try except 로 감싸줘야 중간에 에러나도 다른 파일들은 전부 임베딩 할 수 있다
        try:
            save_pdf(pdf_path)
            print(f"임베딩 완료: {pdf_path}")
        except Exception as e:
            print(f"임베딩 실패: {pdf_path} - {e}")

if __name__ == "__main__":
    main()