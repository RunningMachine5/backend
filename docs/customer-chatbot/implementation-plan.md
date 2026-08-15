# 고객 대응 챗봇 구현 계획

[PRD](README.md)를 코드로 옮기기 위한 **작업 순서 문서**다. 설계의 출처는 여전히
`docs/customer-chatbot/`의 5개 설계 문서이며, 이 문서는 무엇을 어떤 순서로 만들고
각 단계에서 어느 문서 절을 참조하는지만 담는다. 설계와 이 계획이 어긋나면 설계 문서가 이긴다.

작성일: 2026-08-14. 구현이 끝난 단계는 체크박스를 채우고, 전 단계 완료 후 이 문서는
삭제하거나 보관으로 옮긴다.

---

## 0. 현재 상태 (2026-08-14 기준)

### 이미 완료된 것 — 다시 만들지 않는다

| 완료 항목 | 위치 |
| --- | --- |
| 도메인 상수: `customer_action` 19종, `fraud_circumstance` 20종, 검색 질의 매핑, 채점표 | [customer_action_codes.py](../../app/domain/customer_action_codes.py), [fraud_circumstance_codes.py](../../app/domain/fraud_circumstance_codes.py) |
| 테이블 6종 모델 + 등록 | [chatbot.py](../../app/data/model/chatbot.py), [\_\_init\_\_.py](../../app/data/model/__init__.py) |
| 마이그레이션 (이름 변경·신규 생성·백필·부분 유니크 인덱스, `top_fraud_types` 재추가) | `migrations/versions/…c4f7a2b9d810…`, `…d94b7e31a5c2…` |
| 프롬프트 A.1~A.3 렌더링 함수 (도메인 상수에서 조립) | [prompts.py](../../app/services/chatbot/prompts.py) |
| pgvector 코사인 검색 + `MAX_DISTANCE = 0.6` | [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py) |
| 의존성: `langgraph`, `langchain`, `langchain-openai` | `pyproject.toml` |

### 없는 것 — 이 계획이 만드는 것

챗봇 리포지토리, 세션 기반 API, 평가·추출 LLM 호출부, RAG 응답 조립, 채점 집계,
LangGraph 파이프라인, 세션 생성·이메일 발송(콘솔), SSE 반환 경로, FDS 결합.

### 대체·수정 대상

- [app/api/chat.py](../../app/api/chat.py) — 세션 개념 없는 `POST /chat/ask` 하나뿐. 재작성.
- [customer_chatbot_pipeline.py](../../app/pipelines/customer_chatbot_pipeline.py) — Fake 조립 껍데기. 재작성.
- [app/dto/chatbot.py](../../app/dto/chatbot.py) — 구 placeholder dataclass. 재정의.
- [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py) — 0건일 때 문자열
  `"관련 문서를 찾지 못했습니다."` 반환. **구조화가 선결 조건** ([PRD 2.5](README.md#검색-결과-0건-처리)).
- Fake 5종 (`fake_embedder`, `fake_guide_retriever`, `fake_transaction_repository`,
  `fake_llm`, `fake_vector_db`) — 챗봇 파이프라인 참조 제거. 다른 사용처가 없으면 삭제.

### 확인된 사실 (계획에 반영)

- **`transaction_amount` 부호 제약은 이미 해소됐다.** PRD 3.4가 지적한
  `Transaction_Amount: int = Field(gt=0)`는 raw60 계약 정렬 이후 사라졌고, 현재
  [ml_features.py](../../app/dto/ml_features.py)의 `transaction_amount: int`에는 부호 제약이
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
| 7 | API + 세션 생성·이메일 발송 + SSE | 6 |
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
  - `CHAT_LLM_TIMEOUT_SECONDS`, `CHAT_LLM_MAX_ATTEMPTS` — 평가·추출 LLM 호출 공용
- [x] [app/dto/chatbot.py](../../app/dto/chatbot.py) 재정의
  - `CreateChatRequest`(거래 id + `top_fraud_types` 상위 2개 사기유형, 선택) /
    `CreateChatResponse`(세션 id) — PRD 2.1의 표 그대로
  - 평가 판정 결과 DTO — `SUFFICIENT`/`TOO_VAGUE`/`NON_ANSWER`/`REFUSAL`/`WANT_END`
  - 추출 구조화 출력 스키마 — `type` 필드를
    `Literal[*FINAL_CUSTOMER_ACTION_CODES]` / `Literal[*FINAL_FRAUD_CIRCUMSTANCE_CODES]`로
    선언해 파서 단계에서 화이트리스트를 강제 (스키마 3.8)
  - 메시지 송수신·버튼 액션·SSE 이벤트 페이로드 DTO (7단계에서 확장 가능)
  - 구 `ChatbotRequestDTO`/`CustomerGuideDTO`/`ChatbotResponseDTO`는 Fake 파이프라인이
    아직 참조하므로 6단계에서 함께 삭제
- [x] 테스트 `tests/test_chatbot_dto.py`: 화이트리스트 밖 enum이 파싱 실패하는지

## 2단계 — 리트리버 구조화 (선결 조건)

**참조**: [PRD 2.5 검색 결과 0건 처리](README.md#검색-결과-0건-처리),
[스키마 3.10 "손대지 않을 것"](schema.md#310-마이그레이션-적용-순서)

- [x] [chatbot_retriever.py](../../app/services/rag/chatbot_retriever.py)의 `retriever_source`가
  구조화된 결과(청크 내용·출처 제목·페이지·거리의 리스트)를 반환하도록 변경.
  **0건은 빈 리스트**이며 문장을 컨텍스트로 넣지 않는다. `MAX_DISTANCE`·HNSW 인덱스는 유지.
- [x] 호출부 [customer_chatbot.py](../../app/services/chatbot/customer_chatbot.py) 한 곳 갱신
  (문자열 조립을 호출부로 이동). 기존 `POST /chat/ask` 동작은 7단계 재작성 전까지 유지.
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
- [ ] 추출 결과 저장 — `ON CONFLICT DO NOTHING`으로 세션당 enum 1행, 저장 직전
  `code in FINAL_*_CODES` 재검증(불통과 항목만 걸러내고 로그), `evidence_verified` 기록
- [ ] 종료 집계용 조회 — 세션의 `chat_fraud_circumstances` 전체 읽기,
  `fraud_type_score_after_chat` 저장(`ON CONFLICT DO NOTHING`, 거래당 1행)
- [ ] SSE 스냅샷용 조회 — `status = HANDOFF_REQUESTED` 세션 목록 (PRD 2.7)
- [ ] 테스트 `tests/test_chatbot_repository.py`: 멱등 생성, 채택 답변 유일성, enum 중복 무시

## 4단계 — 평가·추출 LLM 서비스

**참조**: [prompts.md A.1~A.3](prompts.md), [PRD 2.4 조건 2](README.md#조건-2-고객응답-평가-llm),
[PRD 3.1](README.md#31-흐름), [스키마 3.6 `evidence_verified`](schema.md#36-추출-결과-테이블--신규)

프롬프트 렌더링은 [prompts.py](../../app/services/chatbot/prompts.py)에 이미 있으므로
**호출부만** 만든다. 프롬프트·문구를 코드에 새로 쓰지 않는다.

- [ ] `app/services/chatbot/answer_evaluator.py` — A.1 평가 호출.
  structured output으로 판정 5종을 강제(프롬프트 지시에 의존하지 않는다, PRD 3.2).
  타임아웃·재시도는 1단계 env var 사용.
  - **실패 폴백은 PRD 3.1의 권장안을 채택한다**: 호출당 타임아웃 + 재시도 상한, 상한 소진 시
    `REFUSAL`과 동일하게 다음 질문으로 진행하고 `verdict_skip_reason = EVALUATOR_FAILED`로
    기록. LLM 실패는 `attempt_no`를 소모하지 않는다. → 확정 내용을 README 2.4·3.1에 반영 (9단계)
- [ ] `app/services/chatbot/extractors.py` — A.2 고객 행동 / A.3 사기 정황 추출 호출.
  structured output 스키마는 1단계 DTO. `evidence`가 답변 원문에 연속 문자열로 존재하는지
  저장 전 대조해 `evidence_verified`를 산출
- [ ] 테스트 `tests/test_chatbot_evaluator.py` / `test_chatbot_extractors.py`:
  LLM 모킹(실호출 금지 — CI는 `OPENAI_API_KEY=test-only-key`), 판정 5종 분기,
  재시도 소진 폴백, evidence 원문 대조 성공·실패

## 5단계 — RAG 응답 조립 + 채점 집계

**참조**: [PRD 2.5](README.md#25-정보-응답--rag-대응-가이드-4-1), [PRD 2.6](README.md#26-사기-정황-추출과-채점-4-2),
[messages.md B.5·B.6](messages.md#b5-근거를-찾지-못한-액션-안내), [scoring.md](scoring.md)

- [ ] `app/services/chatbot/guide_responder.py` (가칭) — PRD 2.5의 의사코드 그대로:
  - 검색 질의 = `CUSTOMER_ACTION_SEARCH_QUERIES[action.type] + " " + action.evidence`
  - Retrieve는 액션당 독립, `top_k = 3` 고정
  - `grounded` / `ungrounded` 분리 → **Generate는 grounded만으로 1회 호출**
    (0건 액션을 프롬프트에 넣지 않아 교차 오염 차단)
  - `assemble`: ungrounded 액션은 `CUSTOMER_ACTION_DESCRIPTIONS` 소제목 + B.5 고정 문구.
    고객이 말한 행동은 근거 유무와 무관하게 전부 목록에 나타난다
  - **전체 액션이 0건이면** handoff 신호를 반환 (B.6 문구, 상태 전이는 파이프라인이 수행)
- [ ] `app/services/chatbot/chat_scoring.py` (가칭) — 상담 종료 시 1회 집계:
  `chat_fraud_circumstances` 전체 × `FRAUD_CIRCUMSTANCE_SCORES` → 4개 유형 점수 전부
  `type_scores`로. 대표 유형·동점·정황 없음은 저장하지 않는다 (스키마 3.7)
- [ ] 외부 조회(더치트·Safe Browsing·경찰청 링크)는 **이번 범위에서 제외** (아래 "제외 범위")
- [ ] 테스트 `tests/test_chatbot_guide_responder.py` / `test_chatbot_scoring.py`:
  리트리버·LLM 모킹, 전체 0건 → handoff, 일부 0건 → B.5 삽입 및 Generate 입력에서 제외,
  집계 합산·동점·최고점 0 분기
- [ ] (선택, 저비용) PRD 3.2 코퍼스 과제 1번: `docs/agent_guides/internal_demo/*_customer.md`
  4종을 `cs_guide_documents`에도 적재해 0건 비율을 낮춘다. 챗봇 로직과 독립이라
  아무 때나 끼워 넣을 수 있다

## 6단계 — LangGraph 파이프라인

**참조**: [PRD 2.3~2.6](README.md#23-최초-알림-메시지와-버튼), [스키마 3.4 대화 진행 상태](schema.md#34-chat_sessions--테이블명-변경-및-컬럼-추가),
[messages.md B.1~B.4](messages.md)

[customer_chatbot_pipeline.py](../../app/pipelines/customer_chatbot_pipeline.py)를 재작성한다.
`StateGraph` + **체크포인터 `InMemorySaver`**, `thread_id = chat_session_id`.
서버 재시작 시 진행 상태 유실은 감수한다(스키마 3.4에 명시된 트레이드오프).

- [ ] 그래프 상태: `question_step`, 현재 질문의 재시도 횟수, 대기 중 여부
- [ ] 노드·엣지 (PRD 2.3~2.6의 흐름 그대로):
  1. 최초 알림(B.1: 거래시각·금액·입금/출금 — 금액 부호로 판정) + 버튼 3종 분기(B.2):
     챗봇 상담 → `IN_PROGRESS`, 상담사 연결 → `HANDOFF_REQUESTED`, 종료 → `DONE`
  2. 질문 출력 — `question_step` 1은 시작 멘트 + 유형판별 질문(`top_fraud_types`
     조합 6종, 없으면 일반 질문 폴백), 2 이상은 추가 질문 멘트 반복(상한 없음)
  3. 답변 평가 — 4단계 서비스 호출. 판정별 전이는 PRD 2.4 표 그대로
     (`TOO_VAGUE`/`NON_ANSWER`는 재질문 최대 2회, 초과 시 마지막 응답 채택
     `is_adopted = true` 후 다음 질문)
  4. `SUFFICIENT` → 추출(A.2·A.3) 저장 + RAG 응답(5단계) → 다음 질문 루프
  5. `WANT_END` → 채점 집계(5단계) + `DONE` + `completed_at`
  6. 전체 액션 0건 → `HANDOFF_REQUESTED` + SSE 발행(7단계 훅)
- [ ] 트랜잭션 소유: 턴 단위로 파이프라인이 `commit`/`rollback`. `question_step`은 턴 종료 시 갱신
- [ ] Fake 참조 제거 및 구 DTO 삭제. `fake_llm`·`fake_vector_db` 등은 다른 사용처(Agent)가
  없는지 확인 후 삭제
- [ ] 테스트 `tests/test_chatbot_pipeline.py`: LLM·RAG 서비스 모킹으로 그래프 분기 검증
  (버튼 3종, 재시도 초과 채택, WANT_END 종료·집계 1회, 전체 0건 handoff)

## 7단계 — API + 세션 생성·이메일 발송 + SSE

**참조**: [PRD 2.1~2.3](README.md#21-채팅-세션-생성-및-이메일-전송), [PRD 2.7](README.md#27-상담사-반환-경로-sse),
[스키마 3.9](schema.md#39-customersemail-확보-경로)

- [ ] 세션 생성 + 발송 서비스 (`app/services/chatbot/session_creator.py` 가칭):
  - 멱등 생성(3단계) → `is_older` 판정(`customers.birth_date` 출생연도 기준 60세 이상)
  - 메일 API 미연동: **콘솔 출력** (PRD 2.1의 형식 그대로)
  - 폴백: `customers.email`이 `NULL`·빈 문자열·공백뿐이면 `CHAT_FALLBACK_EMAIL`로.
    URL은 `CHAT_BASE_URL + /chat/{chat_session_id}`. `notified_email`·`email_sent_at` 기록,
    `status = URL_SENT`
- [ ] [app/api/chat.py](../../app/api/chat.py) 재작성 — 엔드포인트 형태는 설계 문서에 없으므로
  아래는 초안이며 **확정되는 대로 README에 반영한다** (9단계):
  - `POST /chat/sessions` — 세션 생성 (`CreateChatRequest` → `CreateChatResponse`)
  - `POST /chat/{chat_session_id}/verify` — 출생연도 4자리 간이 본인인증
    (실패 횟수 제한·토큰·TTL 없음 — PRD 3.3의 MVP 제외 그대로)
  - `GET /chat/{chat_session_id}` — 세션 상태 + 메시지 이력 (접속, `is_older` 포함)
  - `POST /chat/{chat_session_id}/actions` — 버튼 3종
  - `POST /chat/{chat_session_id}/messages` — 고객 답변 → 파이프라인 실행 → 챗봇 응답
  - 기존 `POST /chat/ask` 제거
- [ ] SSE — `GET /agent/chat-sessions/events` (`text/event-stream`):
  - in-process pub/sub (다중 인스턴스 미고려, MVP 전제)
  - `HANDOFF_REQUESTED` 전이 지점에서 브로드캐스트, 페이로드는
    `chat_session_id`/`transaction_id`
  - 최초 구독 시 현재 `HANDOFF_REQUESTED` 세션 스냅샷 선전송
- [ ] 테스트 `tests/test_chat_api.py`: TestClient로 생성 멱등·본인인증·버튼 상태 전이·폴백
  이메일(콘솔 출력 검증), SSE 스냅샷

## 8단계 — FDS 파이프라인 결합

**참조**: [PRD 3.3](README.md#33-보안운영) — 결합 방식 미정으로 남아 있던 항목의 확정

- [ ] [fraud_detection_pipeline.py](../../app/pipelines/fraud_detection_pipeline.py)에서
  `is_fraud`일 때 세션 생성·발송 호출. **"룰 실패가 ML 결과 저장을 막지 않는다"와 같은
  원칙**으로, 세션 생성 실패는 로그만 남기고 거래 저장을 롤백하지 않는다.
  룰 채점 결과 점수 내림차순 상위 2개를 `top_fraud_types`로 전달하고, 룰 채점이 실패한
  거래는 생략한다(일반 질문 폴백, PRD 2.4)
- [ ] 멱등이므로 `rule_replay` 재처리 경로에서 중복 세션이 생기지 않음을 테스트로 고정
- [ ] 발송이 콘솔 출력뿐이라 동기 호출 지연은 무시 가능. 실제 메일 연동 시 비동기화 재검토
  (README 3.3에 남긴다)
- [ ] 테스트: `tests/test_pipelines.py` 확장 — 세션 생성 실패 주입 시 거래 저장 유지

## 9단계 — 문서 갱신 마감 (스킬 7절 의무)

구현하며 확정한 것을 설계 문서에 반영한다. 각 단계에서 미뤄둔 것의 총정리다.

| 갱신 대상 | 내용 |
| --- | --- |
| README 2.4 / 3.1 | 평가 LLM 실패 폴백 확정 (env var 이름, `EVALUATOR_FAILED` 진행) |
| README 2.1 / 신규 절 | API 엔드포인트 형태 확정본 |
| README 3.3 | FDS 결합 방식 확정 (동기 + 실패 무시 + 멱등), 미해결에서 제거 |
| README 3.4 | `transaction_amount` 부호 제약 해소 반영 (`ml_prediction.py:69` 참조도 갱신) |
| README 1.3 / schema.md 구현 상태 | "비즈니스 로직 없음" 문구를 단계 진행에 맞춰 갱신 |
| README 4 색인 | 절 구성이 바뀌면 색인·상호 링크 정리 (`erd.md` 부재 처리 포함) |
| messages.md / prompts.md | 구현 중 문구·프롬프트가 바뀌었을 때만 |

---

## 채택한 결정 (설계 문서의 권장안·전제를 그대로 따름)

- 평가 LLM 실패 폴백: PRD 3.1의 권장안 (`REFUSAL`과 동일 진행 + `EVALUATOR_FAILED`)
- LangGraph 체크포인터: `InMemorySaver`, 재시작 유실 감수 (스키마 3.4)
- FDS 결합: 동기 호출 + 실패 시 거래 저장 유지 + 멱등 (PRD 3.3의 원칙 문장 그대로)
- SSE: in-process pub/sub, 단일 인스턴스 전제 (PRD 2.7)

## 이번 범위에서 제외 (설계 문서가 MVP 제외로 명시한 것)

- 외부 조회: 더치트·Safe Browsing·경찰청 링크 (제휴 미확정 + 슬롯 추출 선행 필요, PRD 3.4)
- 본인인증 보호장치: 실패 횟수 제한·URL 토큰·세션 TTL (PRD 3.3)
- 고령자 전용 UI (프론트 영역, `is_older` 값 반환까지만)
- 담당자 접수·처리 중 상태 (`HANDOFF_REQUESTED` 이후 확장, PRD 3.3)
- history-aware retriever, 액션 수 상한, 청킹 개선, `MAX_DISTANCE` 튜닝 (PRD 3.1~3.2)
- `customers.email` 실주소 확보 경로(스키마 3.9의 `customer_email` DTO 필드) — 거래 수집
  영역 변경이라 챗봇 단계와 분리해 별도 작업으로 진행 가능. 폴백만으로 데모는 동작한다

## 테스트 실행

```bash
uv run python -m unittest discover -s tests -v
```

unittest만 쓴다(pytest 아님). LLM·외부 API는 전부 모킹한다 — CI가
`OPENAI_API_KEY=test-only-key`로 돌므로 실호출이 있으면 깨진다.
