# 고객 대응 챗봇 구현 계획

[PRD](README.md)를 코드로 옮기기 위한 **작업 순서 문서**다. 설계의 출처는 여전히
`docs/customer-chatbot/`의 5개 설계 문서이며, 이 문서는 무엇을 어떤 순서로 만들고
각 단계에서 어느 문서 절을 참조하는지만 담는다. 설계와 이 계획이 어긋나면 설계 문서가 이긴다.

작성일: 2026-08-14. 전체 9단계 구현 완료일: 2026-08-16.
완료된 구현의 의사결정과 작업 순서를 추적할 수 있도록 이 문서는 기록으로 보관한다.

---

## 0. 계획 시작 당시 상태 (2026-08-15 기준)

### 이미 완료된 것 — 다시 만들지 않는다

| 완료 항목 | 위치 |
| --- | --- |
| 도메인 상수: `fraud_circumstance` 20종과 채점표 | [fraud_circumstance_codes.py](../../app/domain/fraud_circumstance_codes.py) |
| 테이블 6종 모델 + 등록 | [chatbot.py](../../app/data/model/chatbot.py), [\_\_init\_\_.py](../../app/data/model/__init__.py) |
| 마이그레이션 (대화 스키마, `top_fraud_types`, 가이드 검색 질의 저장 구조) | `migrations/versions/…c4f7a2b9d810…`, `…d94b7e31a5c2…`, `…f8a1b2c3d4e5…` |
| 프롬프트 A.1~A.3 렌더링 함수 (도메인 상수에서 조립) | [prompts.py](../../app/services/chatbot/prompts.py) |
| pgvector 코사인 검색 + `MAX_DISTANCE = 0.6` | [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py) |
| 의존성: `langgraph`, `langchain`, `langchain-openai` | `pyproject.toml` |

### 시작 당시 없었던 것 — 이 계획에서 구현 완료

챗봇 리포지토리, 세션 기반 API, 평가·추출 LLM 호출부, RAG 응답 조립, 채점 집계,
LangGraph 파이프라인, 세션 생성·Agent 통합 이메일 발송, 거래별 세션 상태 조회·변경 SSE,
FDS·Agent 결합.

### 대체·수정 대상

- [app/api/chat.py](../../app/api/chat.py) — 세션 개념 없는 `POST /chat/ask` 하나뿐.
  → 6단계 정리에서 엔드포인트를 걷어내고 빈 라우터만 남겼다. 7단계에서 재작성.
- `customer_chatbot_pipeline.py` — Fake 조립 껍데기. → 6단계 정리에서 삭제. 새로 만든다.
- [app/dto/chatbot.py](../../app/dto/chatbot.py) — 구 placeholder dataclass. → 재정의 완료.
- [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py) — 0건일 때 문자열
  `"관련 문서를 찾지 못했습니다."` 반환. **구조화가 선결 조건** ([PRD 2.5](README.md#검색-결과-0건-처리)).
  → 2단계 완료, 구 `retriever`는 6단계 정리에서 삭제.
- Fake 5종 (`fake_embedder`, `fake_guide_retriever`, `fake_transaction_repository`,
  `fake_llm`, `fake_vector_db`) — 챗봇 파이프라인 참조 제거. 다른 사용처가 없으면 삭제.
  → 앞의 3종은 삭제, `fake_llm`·`fake_vector_db`는 Agent가 써서 유지.

### 확인된 사실 (계획에 반영)

- **`transaction_amount` 부호 제약은 이미 해소됐다.** PRD 3.4가 지적한
  `Transaction_Amount: int = Field(gt=0)`는 raw60 계약 정렬 이후 사라졌고, 현재
  [ml_features.py](../../app/dto/ml_features.py)의 `transaction_amount: float`에는 부호 제약이
  없다. 음수(출금) 거래가 422로 걸리지 않으므로 [2.3의 입금/출금 판정](README.md#23-최초-알림-메시지와-버튼)은
  바로 구현 가능하다. → README 3.4 해당 항목 갱신 필요 (9단계).
- **`erd.md`가 없다.** PRD 색인과 schema.md가 링크하지만 파일이 존재하지 않는다.
  이 계획의 범위는 아니지만 링크를 정리하든 파일을 만들든 결정이 필요하다.

---

## 단계 개요

의존 방향대로 아래에서 위로 쌓는다(스킬 규칙). 단계마다 unittest를 남기고 커밋을 나눈다.

| 단계 | 내용 | 의존 |
| --- | --- | --- |
| 1 | 설정값(env var) + DTO·구조화 출력 스키마 | — |
| 2 | 리트리버 구조화 (0건 = 빈 리스트) | — |
| 3 | 챗봇 리포지토리 | 1 |
| 4 | 평가·추출 LLM 서비스 | 1 |
| 5 | RAG 응답 조립 + 채점 집계 | 2, 4 |
| 6 | LangGraph 파이프라인 | 3, 4, 5 |
| 7 | API + 세션 생성·이메일 발송 + 거래별 세션 상태 조회·변경 SSE | 6 |
| 8 | FDS 파이프라인 결합 | 7 |
| 9 | 문서 갱신 마감 | 전체 |

1~2단계는 서로 독립이라 순서를 바꿔도 된다. 3~5단계도 상호 독립이다.

---

## 1단계 — 설정값 + DTO

**참조**: [PRD 2.1 발송 폴백](README.md#발송-구현과-기본-주소-폴백), [PRD 3.1 평가 LLM 실패](README.md#31-흐름),
[스키마 3.8](schema.md#38-appdomain-enum-코드-상수화)

- [x] [app/core/config.py](../../app/core/config.py)에 env var 추가 (`ML_SERVING_*` 패턴 복제,
  settings 클래스 없음) + `.env.example` 갱신
  - `CHAT_BASE_URL` (기본 `http://localhost:8000`)
  - `CHAT_FALLBACK_EMAIL` (기본 `abcd@kosa.com`)
  - `CHAT_LLM_TIMEOUT_SECONDS` (기본 `30`), `CHAT_LLM_MAX_ATTEMPTS` — 평가·추출 LLM 호출 공용.
    가장 느린 가이드 검색 질의 분해(A.2)가 최악 4.9초라 그 아래로 잡으면 모든 턴이
    타임아웃으로 실패한다(PRD 2.4 「모델·reasoning effort와 타임아웃 예산」)
  - `CHAT_LLM_MODEL` (기본 `gpt-5.6-luna`) — 네 LLM 호출 공용 모델
  - `CHAT_RESPONSE_LLM_MODEL` (기본 `gpt-5.6-luna`) — 기본값은 위와 같다. 고객에게 나가는
    대응 가이드 생성(A.4)만 따로 갈아끼울 여지를 두려고 변수를 남겨뒀다
  - `CHAT_LLM_REASONING_EFFORT` (기본 `low`) — 네 LLM 호출 공용. 추론 토큰이 지연을
    지배하므로 모델 크기보다 이 값이 응답 시간을 좌우한다. `minimal`은 평가 LLM이
    오판해 쓰지 않는다
- [x] [app/dto/chatbot.py](../../app/dto/chatbot.py) 재정의
  - `CreateChatRequest`(거래 id + `top_fraud_types` 상위 2개 사기유형, 선택) — PRD 2.1의 표.
    세션 생성이 HTTP 경로를 갖지 않게 되면서 요청 본문이 아니라 생성 함수의 입력 검증이
    됐고, 짝이던 `CreateChatResponse`는 7단계에서 제거했다
  - 평가 판정 결과 DTO — `SUFFICIENT`/`TOO_VAGUE`/`WANT_END`
  - 가이드 검색 질의 구조화 출력 — `title`, `search_query`, `evidence`와 최대 5개 제한
  - 사기 정황 `type`은 `Literal[*FINAL_FRAUD_CIRCUMSTANCE_CODES]`로 화이트리스트 강제
  - 메시지 송수신·버튼 액션·세션 상태 변경 SSE 이벤트 DTO (7단계에서 확장 가능)
  - 구 `ChatbotRequestDTO`/`CustomerGuideDTO`/`ChatbotResponseDTO`는 6단계 정리에서
    Fake 파이프라인과 함께 삭제 완료
- [x] 테스트 `tests/test_chatbot_dto.py`: 가이드 검색 질의 길이·개수와 사기 정황 enum 검증

## 2단계 — 리트리버 구조화 (선결 조건)

**참조**: [PRD 2.5 검색 결과 0건 처리](README.md#검색-결과-0건-처리),
[스키마 3.10 "손대지 않을 것"](schema.md#310-마이그레이션-적용-순서)

- [x] [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py)의 `retriever_source`가
  구조화된 결과(청크 내용·출처 제목·페이지·거리의 리스트)를 반환하도록 변경.
  **0건은 빈 리스트**이며 문장을 컨텍스트로 넣지 않는다. `MAX_DISTANCE`·HNSW 인덱스는 유지.
- [x] 유일한 호출부였던 `customer_chatbot.py`는 6단계 정리에서 삭제했다. 문자열을 돌려주던
  옛 `retriever`도 함께 제거해 `retriever_source` 하나만 남았다.
- [x] 테스트 `tests/test_chatbot_retriever.py`: 임베딩 함수를 모킹해 0건 → 빈 리스트,
  거리 초과 청크 제외 분기

## 3단계 — 챗봇 리포지토리

**참조**: [스키마 3.4~3.7](schema.md#34-chat_sessions--테이블명-변경-및-컬럼-추가), [PRD 2.6 중복 방지](README.md#채점-시점과-중복-방지)

`app/repositories/chat_session.py` 신규 (필요시 `chat_message.py` 등 분리).
**commit 하지 않는다** — 트랜잭션은 파이프라인 소유 (`get_session` 패턴).

- [x] 세션 생성 — `transaction_id` UNIQUE 기반 **멱등**: 이미 있으면 기존 세션 반환
  (PRD 3.3의 `rule_replay` 재처리 대비). 요청의 `top_fraud_types`(선택)를 세션에 저장
- [x] 상태 전이, `question_step` 갱신(턴 종료 시), `email_sent_at`/`notified_email`/
  `completed_at` 기록
- [x] 메시지 저장(순수 로그) + `chat_answers` 기록 — `attempt_no` 1~3,
  `quality_verdict`/`verdict_skip_reason`, 질문당 `is_adopted = true` 정확히 하나
  (부분 유니크 인덱스 준수)
- [x] 추출 결과 저장 — `ON CONFLICT DO NOTHING`으로 세션당 enum 1행, 저장 직전
  `code in FINAL_*_CODES` 재검증(불통과 항목만 걸러냄)
- [x] 종료 집계용 조회 — 세션의 `chat_fraud_circumstances` 전체 읽기,
  `fraud_type_score_after_chat` 저장(`ON CONFLICT DO NOTHING`, 거래당 1행)
- [x] 거래별 세션 상태 조회 — `transaction_id`로 해당 세션의 현재 `status` 조회 (PRD 2.7)
- [x] 테스트 `tests/test_chatbot_repository.py`: 멱등 생성, 채택 답변 유일성, enum 중복 무시

## 4단계 — 평가·추출 LLM 서비스

**참조**: [prompts.md A.1~A.3](prompts.md), [PRD 2.4 조건 2](README.md#조건-2-고객응답-평가-llm),
[PRD 3.1](README.md#31-흐름), [스키마 3.6](schema.md#36-추출-결과-테이블--신규)

프롬프트 렌더링은 [prompts.py](../../app/services/chatbot/prompts.py)에 이미 있으므로
**호출부만** 만든다. 프롬프트·문구를 코드에 새로 쓰지 않는다.

- [x] `app/services/chatbot/answer_evaluator.py` — A.1 평가 호출.
  structured output으로 판정 3종을 강제(프롬프트 지시에 의존하지 않는다, PRD 3.2).
  타임아웃·재시도는 1단계 env var 사용.
  - **실패 폴백은 PRD 3.1의 권장안을 채택한다**: 호출당 타임아웃 + 재시도 상한,
    상한 소진 시 고객 판정과 분리된 경로로 다음 질문에 진행하고
    `verdict_skip_reason = EVALUATOR_FAILED`로 기록한다. → 확정 내용을 README 2.4·3.1에 반영 (9단계)
- [x] `app/services/chatbot/extractors.py` — A.2 가이드 검색 질의 분해 / A.3 사기 정황 추출 호출.
  structured output 스키마는 1단계 DTO. `evidence`가 답변 원문에 연속 문자열로 존재하는지
  저장 전 대조하고, 불일치 항목은 로그를 남긴 뒤 저장하지 않음
- [x] 테스트 `tests/test_chatbot_evaluator.py` / `test_chatbot_extractors.py`:
  LLM 모킹(실호출 금지 — CI는 `OPENAI_API_KEY=test-only-key`), 판정 3종 분기,
  재시도 소진 폴백, evidence 원문 대조 성공·실패

## 5단계 — RAG 응답 조립 + 채점 집계

**참조**: [PRD 2.5](README.md#25-정보-응답--rag-대응-가이드-4-1), [PRD 2.6](README.md#26-사기-정황-추출과-채점-4-2),
[messages.md B.5](messages.md#b5-안내를-만들지-못한-정보-요구-안내), [scoring.md](scoring.md)

- [x] [prompts.md A.4](prompts.md#a4-대응-가이드-생성-프롬프트) 신설 — Generate 프롬프트가
  설계 문서에 없었다. 소제목·목록 조립은 LLM이 하지 않고 코드가 한다는 것을 문서에 못박고,
  [prompts.py](../../app/services/chatbot/prompts.py)의 `render_guide_response_prompt`로 옮겼다
- [x] [guide_responder.py](../../app/services/chatbot/guide_responder.py) — PRD 2.5의 의사코드 그대로:
  - 검색 질의 = 분해 결과의 독립적인 `guide_search_query.search_query`
  - Retrieve는 가이드 검색 질의당 독립, `top_k = 3` 고정
  - `grounded` / `ungrounded` 분리 → **Generate는 grounded만으로 1회 호출**
    (0건 요구를 프롬프트에 넣지 않아 교차 오염 차단)
  - `assemble`: ungrounded 요구는 분해 결과의 `title` 소제목 + B.5 고정 문구.
  - **`GuideResponder`는 상태 전이 신호를 내지 않는다.** 전체 0건이면 Generate를 건너뛰고
    모든 요구를 B.5로 채운다. 요구가 하나도 없으면 빈 본문을 돌려주고 파이프라인이
    메시지를 보내지 않는다 (README 2.5 4번)
  - 검색·생성 실패 폴백을 확정하고 README 2.5에 반영했다(요구별 검색 실패는 그 요구만 0건,
    Generate 실패·전원 빈 안내도 B.5로 채우고 상담 계속)
  - B.5 문구는 [messages.py](../../app/services/chatbot/messages.py)로 옮겼다(B.1~B.4·B.6은 6단계)
- [x] [chat_scoring.py](../../app/services/chatbot/chat_scoring.py) — 상담 종료 시 1회 집계:
  `chat_fraud_circumstances` 전체 × `FRAUD_CIRCUMSTANCE_SCORES` → 4개 유형 점수 전부
  `type_scores`로. 대표 유형·동점·정황 없음은 저장하지 않는다 (스키마 3.7)
- [x] 외부 조회(더치트·Safe Browsing·경찰청 링크)는 **이번 범위에서 제외** (아래 "제외 범위")
- [x] 테스트 `tests/test_chatbot_guide_responder.py` / `tests/test_chatbot_scoring.py`:
  리트리버·LLM 모킹, 전체 0건 → LLM 미호출 + 전원 B.5, 일부 0건 → B.5 삽입 및
  Generate 입력에서 제외, 생성 실패 → B.5 폴백, 가이드 검색 질의 0개 → 빈 본문,
  집계 합산·동점·최고점 0 분기
- [ ] (선택, 저비용) PRD 3.2 코퍼스 과제 1번: `docs/agent_guides/internal_demo/*_customer.md`
  4종을 `cs_guide_documents`에도 적재해 0건 비율을 낮춘다. 챗봇 로직과 독립이라
  아무 때나 끼워 넣을 수 있다

## 6단계 — LangGraph 파이프라인

**참조**: [PRD 2.3~2.6](README.md#23-최초-알림-메시지와-버튼), [스키마 3.4 대화 진행 상태](schema.md#34-chat_sessions--테이블명-변경-및-컬럼-추가),
[messages.md B.1~B.4](messages.md)

`app/pipelines/customer_chatbot_pipeline.py`를 새로 만든다(Fake 껍데기는 삭제됨).
`StateGraph` + **체크포인터 `InMemorySaver`**, `thread_id = chat_session_id`.
서버 재시작 시 진행 상태 유실은 감수한다(스키마 3.4에 명시된 트레이드오프).

- [x] 그래프 상태: `question_step`, 현재 질문의 재시도 횟수.
  체크포인트가 없는 첫 턴·서버 재시작 후에는 `chat_sessions.question_step`에서 seed 하고
  재시도 횟수만 유실을 감수한다. 답변 수신 가능 여부는 영속된 세션 상태와
  `question_step`으로 검증한다
- [x] 노드·엣지 (PRD 2.3~2.6의 흐름 그대로):
  1. 최초 알림(B.1: 거래시각·금액·입금/출금 — 금액 부호로 판정) + 버튼 3종 분기(B.2):
     챗봇 상담 → `IN_PROGRESS`, 상담사 연결 → `HANDOFF_REQUESTED`, 종료 → `DONE`
  2. 질문 출력 — `question_step` 1은 시작 멘트 + 유형판별 질문(`top_fraud_types`
     조합 6종, 없으면 일반 질문 폴백), 2 이상은 추가 질문 멘트 반복(상한 없음)
  3. 답변 평가 — 4단계 서비스 호출. 판정별 전이는 PRD 2.4 표 그대로
     (`TOO_VAGUE`는 재질문 최대 2회, 초과 시 마지막 응답 채택
     `is_adopted = true` 후 다음 질문)
  4. `SUFFICIENT` → 가이드 검색 질의 분해·저장·RAG와 사기 정황 추출·저장을 독립 실행한다.
     한 경로가 재시도 후 실패해도 성공한 경로는 반영하고 다음 질문으로 진행한다
  5. `WANT_END` → 채점 집계(5단계) + `HANDOFF_REQUESTED` + `completed_at`
     + 상태 변경 SSE 발행(7단계 훅). 문구는 B.6
  6. `HANDOFF_REQUESTED` 진입 경로는 1번 버튼과 5번 둘뿐이다. 검색 0건·LLM 실패는
     상태를 전이시키지 않는다 (PRD 2.5)
- [x] 트랜잭션 소유: 턴 단위로 파이프라인이 `commit`/`rollback`. `question_step`은 턴 종료 시 갱신
- [x] Fake 참조 제거 및 구 DTO 삭제 완료. 삭제한 것: `customer_chatbot_pipeline.py`,
  `fake_embedder.py`, `fake_guide_retriever.py`, `fake_transaction_repository.py`,
  `customer_chatbot.py`, 구 `retriever`, 구
  `ChatbotRequestDTO`/`CustomerGuideDTO`/`ChatbotResponseDTO`,
  `FakeLLM.generate_chatbot_answer`, `ConsoleRenderer.print_chatbot_response`,
  `CUSTOMER_GUIDE_TEXT`, `tests/test_pipelines.py`의 챗봇 테스트.
  남긴 것: `fake_llm.py`·`fake_vector_db.py` — Agent의 `monitoring_agent_pipeline.py`가 쓴다.
  `app/api/chat.py`는 빈 라우터만 남겨 7단계에서 재작성한다.
- [x] 테스트 `tests/test_chatbot_pipeline.py`: LLM·RAG 서비스 모킹으로 그래프 분기 검증
  (버튼 3종, 재시도 초과 채택, WANT_END 집계 1회 + `HANDOFF_REQUESTED` 전이,
  전체 0건이어도 상태 불변)
- [x] `ChatSessionRepository.request_handoff` 신설 — 기존 `set_session_complete`가
  `DONE` 고정이라 `HANDOFF_REQUESTED` 전이 경로가 없었다. `completed_at`은 선택이며
  버튼 경로는 남기지 않고 `WANT_END`만 기록한다

## 7단계 — API + 세션 생성·이메일 발송 + 거래별 세션 상태 조회·변경 SSE

**참조**: [PRD 2.1~2.3](README.md#21-채팅-세션-생성-및-이메일-전송), [PRD 2.7](README.md#27-상담사-반환-경로-거래별-상태-조회--sse),
[스키마 3.9](schema.md#39-customersemail-확보-경로)

- [x] 세션 생성 서비스 ([session_creator.py](../../app/services/chatbot/session_creator.py)):
  - 멱등 생성(3단계) → `is_older` 판정(`customers.birth_date` 출생연도 기준 60세 이상)
  - 8단계 결합에서 단독 B.7 메일을 제거했다. Agent의 기존 이상거래 안내 메일에 세션 URL을
    넣어 한 통만 보내며 [session_alert_notifier.py](../../app/services/chatbot/session_alert_notifier.py)가
    생성·발송·상태 기록을 조정한다
  - 폴백: `customers.email`이 `NULL`·빈 문자열·공백뿐이면 `CHAT_FALLBACK_EMAIL`로.
    URL은 `CHAT_BASE_URL + /chat/{chat_session_id}`. `notified_email`·`email_sent_at` 기록,
    `status = URL_SENT`
- [x] [app/api/chat.py](../../app/api/chat.py) 재작성 — 엔드포인트 형태는 설계 문서에 없었으므로
  초안으로 만들었고, 확정된 형태를 README에 반영했다:
  - ~~`POST /chat/sessions`~~ — **만들지 않는다.** 세션 생성의 운영 호출자는 Agent뿐이라
    함수 호출로 충분하다(PRD 2.1). 초안 단계에서 한 번 만들었다가 제거했다. 로컬에서 접속
    URL이 필요하면 [scripts/create_chat_session.py](../../scripts/create_chat_session.py)를
    쓴다(PRD 2.1 테스트용 세션 생성)
  - `POST /chat/{chat_session_id}/verify` — 출생연도 4자리 간이 본인인증
    (실패 횟수 제한·토큰·TTL 없음 — PRD 3.3의 MVP 제외 그대로)
  - `GET /chat/{chat_session_id}` — 세션 상태 + 메시지 이력 (접속, `is_older` 포함)
  - `POST /chat/{chat_session_id}/actions` — 버튼 3종
  - `POST /chat/{chat_session_id}/messages` — 고객 답변 → 파이프라인 실행 → 챗봇 응답
  - (기존 `POST /chat/ask`는 6단계 정리에서 이미 제거했다)
  - 확정된 엔드포인트 6종은 [README 2.8](README.md#28-api-엔드포인트)에 표로 남겼고,
    Swagger(`/docs`)에 한국어 summary·description·오류 예시를 달았다
- [x] 담당자 거래 목록에서 각 `transaction_id`에 연결된 채팅 세션 상태 조회 API
- [x] SSE — `GET /agent/chat-sessions/events` (`text/event-stream`):
  - 대시보드당 연결 하나로 모든 세션 상태 변경을 수신하고 `transaction_id`로 목록 항목 갱신
  - 페이로드는 `transaction_id`/`chat_session_id`/`status`
  - 전체 세션 스냅샷은 보내지 않음. 최초 접속·재연결 시 거래별 상태 조회로 현재값 복구
  - in-process pub/sub (다중 인스턴스 미고려, MVP 전제)
- [x] 테스트 [tests/test_chat_api.py](../../tests/test_chat_api.py): TestClient로 본인인증·버튼
  상태 전이, 상태에 맞지 않는 입력의 `409`, 거래별 세션 상태 조회, 상태 변경 SSE 프레임.
  세션 생성 멱등·폴백 이메일은 `tests/test_chat_session_creator.py`가, 스크립트 인자 계약은
  `tests/test_create_chat_session_script.py`가 맡는다
  - **SSE 만 TestClient 로 열지 않는다.** 끝나지 않는 스트림이라 `client.stream(...)` 이
    연결을 닫을 때 매달린다. 라우터가 만든 응답 본문 이터레이터를 직접 읽어 프레임을 본다
  - 평가 LLM 이 필요한 턴은 파이프라인이 지연 생성하는 `AnswerEvaluator` 자리를 대역으로
    바꾼다. 라우터에 서비스 주입 지점이 없어 생성자 주입 대신 패치를 쓴다

## 8단계 — FDS·Agent 파이프라인 결합

**참조**: [PRD 3.3](README.md#33-보안운영) — 결합 방식 미정으로 남아 있던 항목의 확정

- [x] FDS는 원본 거래와 ML·룰 결과를 먼저 커밋하고 이상거래일 때 Agent 백그라운드 작업을
  등록한다. 룰 점수 결과가 없으면 Agent 입력을 만들지 않으므로 세션도 생성하지 않는다.
  세션 생성 실패는 Agent 이메일 노드가 로그로 격리해 저장된 거래를 롤백하지 않는다
- [x] **첫 상태 SSE는 Agent 작업 실행기가 맡는다.** Agent 사건과 세션 상태 커밋이 끝난 뒤
  [task_runner.py](../../app/services/agent/task_runner.py)가 세션을 조회해 `URL_SENT` 또는
  `FAILED`를 발행한다(PRD 2.7)
- [x] **고객 안내 메일은 한 통으로 합쳤다.** Agent 워크플로가
  [`_send_alert_email`](../../app/services/agent/workflow.py#L313)로 이상거래 안내 메일을
  보내기 전에 세션을 만들고 `/chat/{chat_session_id}` URL을 `chatbot_url`로 넘긴다.
  단독 B.7 메일러와 `CUSTOMER_CHATBOT_URL` 고정 주소는 제거했다.
  `AgentEmailRepository`는 고객 이메일이 비어 있으면 `CHAT_FALLBACK_EMAIL`을 사용한다
- [x] 실제 DB 통합 테스트로 같은 거래의 세션이 한 행이고 Agent 통합 메일이 한 번만
  나가는 것을 고정했다
- [x] SMTP 발송은 Agent 백그라운드 작업 안에서 동기 실행한다. 실제 작업 큐·자동 재시도를
  도입할 때 비동기화와 재발송 정책을 재검토한다(README 3.3)
- [x] 통합 테스트: 메일 실패 시 기존 거래는 유지하고 챗봇 세션만 `FAILED`로 기록

## 9단계 — 문서 갱신 마감 (스킬 7절 의무)

구현하며 확정한 것을 설계 문서에 반영한다. 각 단계에서 미뤄둔 것의 총정리다.

| 갱신 대상 | 내용 |
| --- | --- |
| ~~README 2.4 / 3.1~~ | ~~평가 LLM 실패 폴백 확정 (env var 이름, `EVALUATOR_FAILED` 진행)~~ — 6단계에서 반영 완료 |
| ~~README 2.5~~ | ~~검색·생성 실패 폴백~~ — 5단계에서 반영 완료 |
| ~~messages.md B.1 / B.4~~ | ~~B.1 치환 표기 형식, B.4를 재시도 소진·평가 장애 공통 전이 안내로 확정~~ — 반영 완료 |
| ~~README 2.1 / 신규 절~~ | ~~API 엔드포인트 형태 확정본~~ — 7단계에서 [2.8 신설](README.md#28-api-엔드포인트)로 반영 완료 |
| ~~README 3.3~~ | ~~FDS 결합 방식 확정~~ — 거래 커밋 후 Agent 백그라운드 실행 + 실패 격리로 반영 완료 |
| ~~README 3.4~~ | ~~`transaction_amount` 부호 제약 해소 및 현재 `ml_features.py` 계약 반영~~ — 완료 |
| ~~README 1.3 / schema.md 구현 상태~~ | ~~스키마뿐 아니라 애플리케이션 흐름까지 완료된 현재 상태 반영~~ — 완료 |
| ~~README 4 색인~~ | ~~절 구성과 상호 링크 정리, 존재하지 않는 `erd.md` 링크 제거~~ — 완료 |
| ~~messages.md~~ / prompts.md | ~~단독 B.7 안내 문구 제거~~. 프롬프트는 변경 없음 |

9단계 완료와 함께 이 구현 계획의 모든 단계가 종료됐다. 이후 기능 확장은
README 3장의 미해결 문제와 이번 범위 제외 항목을 새 작업의 출발점으로 삼는다.

---

## 채택한 결정 (설계 문서의 권장안·전제를 그대로 따름)

- 평가 LLM 실패 폴백: 고객 판정과 분리해 다음 질문 진행 + `EVALUATOR_FAILED`
- LangGraph 체크포인터: `InMemorySaver`, 재시작 유실 감수 (스키마 3.4)
- LangGraph 구동: **턴 단위 invoke**. 고객 입력 하나 = 그래프 실행 하나이고 그 턴의
  출력을 만든 뒤 END로 끝난다. `interrupt()` + `Command(resume=...)`로 대화 중간에
  멈춰 세우지 않는다 — 입력 경로가 HTTP 요청뿐이라 멈춤 지점이 곧 요청 경계이고,
  `InMemorySaver`에서는 재시작 시 멈춘 노드 자체가 사라져 재개할 수 없다 (스키마 3.4)
- `is_adopted`: `SUFFICIENT`와 재시도 초과에만 `true` (README 2.4 `is_adopted`를 세우는 판정)
- 재시도 초과 턴의 고객 출력: B.4 다음 질문 전환 안내 (messages.md B.4)
- FDS 결합: 거래·탐지 결과 커밋 후 Agent 백그라운드 실행 + 실패 시 거래 저장 유지
- 고객 안내 메일: **Agent 이상거래 안내 메일에 세션 URL을 주입해 한 통으로 보낸다**(8단계).
  단독 챗봇 B.7 메일과 고정 URL은 사용하지 않는다
- SSE: 대시보드당 연결 하나 + in-process pub/sub, 재연결 시 거래별 상태 재조회 (PRD 2.7)

## 이번 범위에서 제외 (설계 문서가 MVP 제외로 명시한 것)

- 외부 조회: 더치트·Safe Browsing·경찰청 링크 (제휴 미확정 + 슬롯 추출 선행 필요, PRD 3.4)
- 본인인증 보호장치: 실패 횟수 제한·URL 토큰·세션 TTL (PRD 3.3)
- 고령자 전용 UI (프론트 영역, `is_older` 값 반환까지만)
- 담당자 접수·처리 중 상태 (`HANDOFF_REQUESTED` 이후 확장, PRD 3.3)
- history-aware retriever, 청킹 개선, `MAX_DISTANCE` 튜닝 (PRD 3.1~3.2)
- `customers.email` 실주소 확보 경로(스키마 3.9의 `customer_email` DTO 필드) — 거래 수집
  영역 변경이라 챗봇 단계와 분리해 별도 작업으로 진행 가능. 폴백만으로 데모는 동작한다

## 테스트 실행

```bash
uv run python -m unittest discover -s tests -v
```

unittest만 쓴다(pytest 아님). LLM·외부 API는 전부 모킹한다 — CI가
`OPENAI_API_KEY=test-only-key`로 돌므로 실호출이 있으면 깨진다.
