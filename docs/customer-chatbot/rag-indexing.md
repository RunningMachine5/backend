# 고객 대응 가이드 RAG 인덱싱

고객 대응 가이드 PDF는 로컬 Unstructured의 `partition_pdf(strategy="fast")`로
요소를 추출한 뒤 `chunk_by_title()`로 의미 단위 청킹한다. 외부 Unstructured API,
OCR, `hi_res`는 이 절차의 범위가 아니다.

## 설치와 실행

PDF 처리 의존성은 `indexing` dependency group에만 있다. 운영 API 이미지의
`uv sync --no-dev`에는 포함되지 않는다.

```bash
uv sync --locked --group indexing
uv run --group indexing --env-file .env python -m app.services.rag.docs_embedding
```

인덱서는 평가에서 채택한 U-1200 설정만 사용한다. 청킹 전략이나 프로필을 선택하는
CLI 옵션은 제공하지 않는다.

마지막 위치 인자로 다른 PDF 디렉터리를 지정할 수 있다. 디렉터리를 생략하면
`docs/embed_target_pdfs`를 사용한다.

## 청킹 설정

| hard max | soft max | 작은 섹션 결합 | oversized overlap |
| ---: | ---: | ---: | ---: |
| 1,200자 | 800자 | 200자 미만 | 100자 |

`multipage_sections=False`, `overlap_all=False`를 사용한다. 따라서 청크는 페이지를
넘지 않고, 정상적인 제목 경계 사이에는 겹침을 추가하지 않는다. `overlap`은 hard
max를 넘은 요소를 텍스트 분할할 때만 적용된다.

Unstructured `fast`도 텍스트 레이어가 없으면 OCR로 폴백하므로, 인덱서는 먼저
텍스트 추출 가능 여부를 검사한다. 이미지 전용 PDF는 Unstructured를 호출하지 않고
실패·제외 목록에 기록한다. 현재 코퍼스에서는 `C01.pdf`가 이에 해당하며 기존
적재분이 있다면 그대로 보존한다.

현재 한국어 코퍼스 검증 버전은 `unstructured[pdf]>=0.18.15,<0.19`다.
`0.27.1`의 `fast`는 `pdfminer`로 텍스트가 추출되는 일부 파일에서도 빈 요소를
반환했기 때문에 사용하지 않는다. 버전 범위를 변경할 때는 전체 43개 PDF의 성공·제외
목록과 아래 평가를 다시 확인해야 한다.

## 원자적 교체와 실패 처리

문서 하나의 파싱과 모든 청크 임베딩이 끝난 뒤에만 DB 세션을 연다. 청크 수와
임베딩 수가 같은지 확인한 후, 같은 제목의 기존 문서 삭제와 신규 문서·청크 삽입을
하나의 트랜잭션에서 실행한다.

다음 상황에서는 신규 데이터로 교체하지 않고 기존 문서를 유지한다.

- 추출 또는 청킹 결과가 비어 있음
- 청크 수와 임베딩 수가 다름
- DELETE 또는 INSERT를 포함한 DB 작업 실패

한 문서의 실패가 나머지 문서 인덱싱을 중단시키지는 않는다. 프로세스 종료 시 파일명,
예외 유형, 원인이 실패 목록으로 출력된다. 예상된 `C01.pdf` 제외 때문에 전체 코퍼스
실행은 종료 코드 1을 반환할 수 있으므로, 성공 42개와 실패·제외 1개인지 함께 확인한다.

## U-1200 선택 근거

2026-08-22 로컬 43개 PDF 평가에서 U-1200은 텍스트 처리 가능한 42개 문서를
1,382개 청크로 만들었다. 고정 골든 질의 기준 `precision@3` 0.2000, locator
`recall@3` 0.4500을 기록해 최종 설정으로 채택했다. 이후 검색·응답 품질 평가는
`app.scripts.evaluate_rag_ragas`를 사용한다.

## Cohere 리랭킹

운영 검색은 `text-embedding-3-small`과 pgvector 코사인 거리로 `0.6` 이내 후보를
최대 30개 가져온 뒤, Cohere `rerank-v4.0-fast`가 질의와 청크 본문만 비교해 최종
5개를 고른다. 검색 질의 원문이나 청크를 별도로 재작성하지 않는다.

`COHERE_RERANK_ENABLED=false`로 두면 Cohere 를 전혀 호출하지 않고 후보도 `top_k`
개만 조회한다. 의도적으로 끈 상태이므로 안내는 프로세스당 한 번 INFO 로만 남긴다.
평가 스크립트의 `--rerank-rpm`도 이때는 무시되고, `check_cohere_reranker`는 연결
확인은 그대로 하되 챗봇 경로가 리랭커를 부르지 않는다고 먼저 경고한다.

키가 비었거나 네트워크·응답 검증에 실패하면 (플래그가 켜져 있어도) 기존 벡터
거리순 결과로 물러나고 경고를 남긴다. 실제 키는 `COHERE_API_KEY`로만 주입하며
저장소에 커밋하지 않는다.

키·모델·네트워크 오류를 고객 데이터나 DB 호출 없이 확인하려면 다음 독립 진단을
실행한다. 키 값은 출력하지 않고 SDK 예외 타입, HTTP 상태와 응답 메시지만 기록한다.

```bash
uv run --env-file .env python -m app.scripts.check_cohere_reranker
```

Cohere 평가용 키의 10 RPM 한도를 넘기지 않고 RAGAS를 실행하려면 실제 리랭커
요청 시작을 9 RPM으로 제한한다. 재시도 요청도 이 제한 횟수에 포함된다.

```bash
uv run --env-file .env --group eval python -m app.scripts.evaluate_rag_ragas \
  --rerank-rpm 9 --out evals/rag/reports/use_cohere_reranker.json
```

Unstructured 동작 근거는 공식
[Partitioning 문서](https://docs.unstructured.io/open-source/core-functionality/partitioning)와
[Chunking 문서](https://docs.unstructured.io/open-source/core-functionality/chunking)를
참조한다.
