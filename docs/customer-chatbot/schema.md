# 고객 대응 챗봇 — DB·스키마

[고객 대응 챗봇 설계 (PRD)](README.md)의 부속 문서다.
챗봇이 쓰는 테이블·컬럼 정의와 마이그레이션 적용 순서를 담는다.
각 값을 언제 쓰는지는 PRD의 [2. 작동 시나리오](README.md#2-작동-시나리오)에 있다.
전체 테이블 목록과 관계는 [3.2](#32-관련-테이블) 및 각 테이블의 FK·제약조건 설명에서
확인할 수 있다.

## 구현 상태

**챗봇 스키마와 애플리케이션 흐름은 현재 구현되어 있다.**

- `fraud_circumstance` 20종과 유형별 점수표를 `app/domain/`에 정의했다.
- [app/data/model/chatbot.py](../../app/data/model/chatbot.py)에 세션·메시지·답변·가이드 검색 질의·
  사기 정황·채점 결과 모델을 정의하고
  [app/data/model/__init__.py](../../app/data/model/__init__.py)에 등록했다.
- Alembic 초기 스키마 revision `6040d304df8d`에 현재 챗봇 테이블, FK, CHECK,
  UNIQUE, 부분 유니크 인덱스가 모두 포함되어 있다.
- 리포지토리·DTO·LLM 추출과 RAG·LangGraph 파이프라인·세션 API·Agent 통합 이메일까지
  위 스키마를 사용하는 애플리케이션 흐름을 구현했다. 담당자 화면의 상태 확인은 서버 push가
  아니라 거래별 상태 조회 폴링이다(PRD 2.7).

실제 고객 이메일을 거래 수집 시점에 확보하는 경로는 아직 없다. 현재는
[3.9의 기본 주소 폴백](#39-customersemail-확보-경로)으로 데모 동작만 보장하며,
실주소 수집은 거래 수집 영역의 별도 후속 작업이다.

| 절 | 내용 |
| --- | --- |
| [구현 상태](#구현-상태) | 챗봇 영속 스키마 완료 범위 |
| [3.1](#31-사기-유형) | 사기 유형 코드 |
| [3.2](#32-관련-테이블) | 관련 테이블 목록 |
| [3.3](#33-챗봇-상태-정의) | `ChatSessionStatus` 5종 |
| [3.4](#34-chat_sessions--현재-컬럼) | `chat_sessions` 현재 컬럼 |
| [3.5](#35-chat_answers) | `chat_answers` |
| [3.6](#36-추출-결과-테이블) | 가이드 검색 질의·사기 정황 추출 테이블 |
| [3.7](#37-fraud_type_score_after_chat) | `fraud_type_score_after_chat` |
| [3.8](#38-appdomain-enum-코드-상수화) | `app/domain/` enum 코드 상수화 |
| [3.9](#39-customersemail-확보-경로) | `customers.email` 확보 경로 |
| [3.10](#310-마이그레이션-적용-순서) | 마이그레이션 적용 순서 |

미해결로 남은 스키마·계약 문제는 PRD
[3.4 스키마·계약](README.md#34-스키마계약)에 모여 있다.

---

### 3.1 사기 유형

[app/domain/fraud_type_codes.py](../../app/domain/fraud_type_codes.py)의 기존 정의를 그대로 쓴다.

```python
FRAUD_TYPE_DISPLAY_NAMES: Mapping[str, str] = {
    VOICE_PHISHING: "보이스피싱",
    MESSENGER_PHISHING: "메신저피싱",
    ACCOUNT_TAKEOVER: "계정탈취",
    FRAUD_USED_ACCOUNT: "사기이용계좌",
}
```

### 3.2 관련 테이블

| 구분 | 테이블 | 현재 용도 |
| --- | --- | --- |
| 거래 원장 | `transactions` | 세션이 연결되는 원본 거래 |
| 챗봇 세션·메시지 | `chat_sessions`, `chat_messages` | 세션 상태와 대화 원문 |
| 질문·시도·판정 이력 | `chat_answers` | 질문별 응답 시도와 LLM 판정 |
| 추출 결과 | `chat_guide_search_queries`, `chat_fraud_circumstances` | RAG 검색 질의와 사기 정황 |
| 채팅 후 사기유형 점수 | `fraud_type_score_after_chat` | 상담 종료 시 집계한 4개 유형 점수 |
| 고객 대응가이드 임베딩 | `cs_guide_documents`, `cs_guide_document_chunks` | 챗봇 RAG 검색 코퍼스 |

### 3.3 챗봇 상태 정의

`chat_sessions.status` (`ChatSessionStatus`)는 다음 5개 값을 가진다.

| 값 | 의미 |
| --- | --- |
| `URL_SENT` | 챗봇 URL 전송 |
| `IN_PROGRESS` | 챗봇 상담 진행중 |
| `HANDOFF_REQUESTED` | 상담사 연결 요청 |
| `DONE` | 챗봇 상담 완료 |
| `FAILED` | 챗봇 상담 실패 |

별도의 "생성됨" 상태는 두지 않는다. 세션 생성 시 기본값은 `URL_SENT`지만 운영 경로에서는
Agent 통합 메일 결과가 정해질 때까지 세션을 커밋하지 않는다. 발송 성공은 그대로
`URL_SENT`, 실패는 `FAILED`로 바꾼 뒤 Agent 사건 상태와 함께 커밋한다.

`FAILED`로 분기하는 경로는 **세션 URL을 포함한 Agent 안내 메일 발송 실패 하나**다
([2.1 발송 구현](README.md#발송-구현과-기본-주소-폴백)). 고객이 URL을 받지 못해 챗봇이 시작될 수
없는 상태이므로 `URL_SENT`로 둘 수 없다. 고객 이메일이 없는 경우는 같은 절의 기본 주소
폴백으로 처리되므로 실패가 아니다.

실패 사유를 남길 컬럼(`agent_cases.failure_reason` 패턴)은 아직 두지 않았다. 지금은 분기가
하나뿐이라 `status = FAILED` + `email_sent_at IS NULL`로 사유가 특정되고, 예외 상세는 로그에
남는다. 다른 실패 분기가 생기면 그때 컬럼을 추가한다.

### 3.4 `chat_sessions` — 현재 컬럼

현재 모델은 `chat_sessions`, `chat_messages` 이름을 사용한다. 다음 표는
[app/data/model/chatbot.py](../../app/data/model/chatbot.py)의 전체 현재 컬럼이다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `chat_session_id` | `varchar(64) PK` | `CHAT-YYYYMMDD-XXXXXXXX` 형식의 세션 id |
| `transaction_id` | `bigint NOT NULL` FK → `transactions.id`, `ON DELETE CASCADE`, `UNIQUE` | 거래당 세션 하나 |
| `status` | `varchar(32) NOT NULL` | [3.3](#33-챗봇-상태-정의)의 5개 값. 애플리케이션 기본값은 `URL_SENT` |
| `last_message_id` | `bigint NULL` FK → `chat_messages.message_id`, `ON DELETE SET NULL` | 가장 최근에 저장한 메시지 |
| `is_older` | `boolean NOT NULL` | 출생연도 기준 60세 이상 여부. 애플리케이션 기본값은 `false` |
| `top_fraud_types` | `jsonb NULL` | 룰 채점 점수 내림차순 상위 2개 사기유형 코드([3.1](#31-사기-유형)). [유형판별 질문](README.md#유형판별-질문) 선택에 쓰고, `NULL`이면 일반 질문 폴백 |
| `question_step` | `integer NOT NULL DEFAULT 0` | 현재 질문 단계. 고객 화면 응답과 서버 재시작 뒤 그래프 상태 복구에 사용 |
| `email_sent_at` | `timestamptz NULL` | 세션 URL을 포함한 Agent 안내 메일을 보낸 시각 |
| `notified_email` | `varchar(255) NULL` | 실제로 보낸 수신 주소. 기본 주소 폴백이 있어 `customers.email`과 다를 수 있으므로 보낸 값을 그대로 남긴다 |
| `created_at` | `timestamptz NOT NULL` | 세션 생성 시각 |
| `completed_at` | `timestamptz NULL` | 종료 시각 |

`status`에는 `ck_chat_sessions_status` CHECK와 조회 인덱스
`ix_chat_sessions_status`가 있다. `last_message_id`는 두 테이블이 서로 참조하므로
`chat_sessions`와 `chat_messages`를 만든 뒤 별도 FK로 추가한다.

`chat_messages`의 현재 컬럼은 다음과 같다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `message_id` | `BIGSERIAL PK` | 메시지 순서와 `last_message_id` 갱신에 쓰는 id |
| `chat_session_id` | `varchar(64) NOT NULL` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | 소속 세션 |
| `sender_type` | `varchar(16) NOT NULL` | `AI` / `HUMAN` / `SYSTEM` |
| `message_text` | `text NOT NULL` | 메시지 원문 |
| `sent_at` | `timestamptz NOT NULL` | 저장 시각 |

`sender_type`은 `ck_chat_messages_sender_type` CHECK로 제한하고,
`(chat_session_id, sent_at)`에는 `ix_chat_messages_session_sent_at` 인덱스가 있다.

#### 대화 진행 상태

정보 수집 단계는 LangGraph로 구현되는 상태 기계다(현재 질문이 무엇인지, 조건 1의 재시도
횟수, 평가 결과 대기 중인지 등).

**LangGraph 체크포인터는 `InMemorySaver`를 쓴다.** 진행 상태는 `chat_session_id`를
`thread_id`로 삼아 프로세스 메모리에만 둔다. **서버 재시작·재배포는 고려하지 않는다.**
진행 중이던 세션이 유실되는 것은 MVP에서 감수하는 트레이드오프이며, 진행 상태를 DB에
스냅샷으로 저장하는 컬럼은 두지 않는다.

- 시도 횟수·판정 이력은 [3.5 `chat_answers`](#35-chat_answers)에 영구
  기록된다. 체크포인터의 현재 질문 재시도 횟수는 초기화되지만, 이미 받은 고객 답변과
  판정, 추출 결과는 남는다.
- `question_step`은 `InMemorySaver`의 사본이 아니라 운영 조회용 값이다. 턴이 끝날 때 갱신한다.
  체크포인트가 없는 첫 턴과 재시작 이후에는 이 컬럼이 그래프 상태의 seed가 되므로,
  유실되는 것은 재시도 횟수뿐이다. 답변 수신 가능 여부는 세션 상태와 이 컬럼으로 검증한다
  ([customer_chatbot_pipeline.py](../../app/pipelines/customer_chatbot_pipeline.py)).
- 메모리 체크포인터를 공유할 수 없는 다중 인스턴스 배포도 고려하지 않는다.

### 3.5 `chat_answers`

`chat_messages`는 `sender_type` / `message_text` / `sent_at`만 가진 순수 대화 로그라
다음 정보가 어디에도 남지 않는다.

- 그 답이 몇 번째 시도(재질문 포함)인지
- 평가 LLM 판정 결과(`SUFFICIENT` / `TOO_VAGUE` / `WANT_END`)
- 여러 시도 중 실제로 채택되어 추출과 판정에 쓰인 답이 어느 것인지

이 정보가 없으면 추출/판정 LLM의 입력을 사후에 재구성할 수 없어 재현·디버깅·담당자 검토가
전부 불가능하다. `chat_messages`에 컬럼을 얹는 대신 별도 테이블을 둔다.
`chat_messages`는 지금처럼 순수 대화 로그로 남고, `chat_answers`가 그중
"질문에 대한 답변으로 채택(또는 재시도)된 메시지"만 골라 구조화된 메타데이터를 붙인다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `answer_id` | `BIGSERIAL PK` | |
| `chat_session_id` | `varchar(64) NOT NULL` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `question_step` | `integer NOT NULL` | 질문 진행 단계 |
| `attempt_no` | `integer NOT NULL` | 해당 질문에 대한 시도 번호. 최초 응답이 1, 조건 1에 따라 최대 3 |
| `message_id` | `bigint NOT NULL` FK → `chat_messages.message_id`, `ON DELETE CASCADE`, `UNIQUE` | 이 시도의 고객 답변 원문이 저장된 메시지 |
| `quality_verdict` | `varchar(16) NULL` | `SUFFICIENT` / `TOO_VAGUE` / `WANT_END`. 평가 LLM을 거치지 못했으면 `NULL` |
| `verdict_skip_reason` | `varchar(24) NULL` | 판정을 건너뛴 원인. DB 허용값은 `MAX_RETRY_EXCEEDED` / `EVALUATOR_FAILED`이며, 현재 파이프라인은 평가 LLM 실패 때 `EVALUATOR_FAILED`만 기록 |
| `is_adopted` | `boolean NOT NULL DEFAULT false` | 이 질문에서 최종 선택한 응답인지. `SUFFICIENT`만 추출 입력으로 쓰고, 세 번째 `TOO_VAGUE`는 `true`여도 추출하지 않음 |
| `created_at` | `timestamptz NOT NULL` | |

제약:

```sql
UNIQUE (chat_session_id, question_step, attempt_no)
CHECK (attempt_no BETWEEN 1 AND 3)
CHECK (quality_verdict IS NULL
       OR quality_verdict IN ('SUFFICIENT','TOO_VAGUE','WANT_END'))
CHECK (verdict_skip_reason IS NULL
       OR verdict_skip_reason IN ('MAX_RETRY_EXCEEDED','EVALUATOR_FAILED'))

CREATE INDEX ix_chat_answers_session_step
    ON chat_answers (chat_session_id, question_step);

-- 질문당 채택 답변은 최대 하나
CREATE UNIQUE INDEX uq_chat_answers_adopted
    ON chat_answers (chat_session_id, question_step) WHERE is_adopted;
```

부분 유니크 인덱스는 채택 답변이 **둘 이상 생기지 않는 것**만 강제한다. `SUFFICIENT`와
세 번째 `TOO_VAGUE`는 하나를 채택하지만, `WANT_END`, 재질문 중인 응답, 평가 LLM 실패는
채택하지 않으므로 질문 단계에 `true`가 하나도 없을 수 있다.

현재 재시도 소진 경로는 세 번째 응답의 `quality_verdict = TOO_VAGUE`를 그대로 저장하고
`verdict_skip_reason`을 비운다. `MAX_RETRY_EXCEEDED`는 CHECK와 리포지토리 계약에는 남아 있지만
현재 파이프라인이 쓰지 않는다. 평가 LLM이 재시도 상한까지 실패한 경우만
`quality_verdict = NULL`, `verdict_skip_reason = EVALUATOR_FAILED`로 기록한다.

### 3.6 추출 결과 테이블

동적 가이드 검색 질의는 RAG 검색과 감사 기록에, 사기 정황은 점수 집계에 사용한다.

| 테이블 | PK | 핵심 컬럼 | 중복 방지 |
| --- | --- | --- | --- |
| `chat_guide_search_queries` | `guide_search_query_id BIGSERIAL` | `position`, `title`, `search_query` | `UNIQUE (source_answer_id, position)` |
| `chat_fraud_circumstances` | `circumstance_id BIGSERIAL` | `circumstance_code varchar(64)` | `UNIQUE (chat_session_id, circumstance_code)` |

`chat_guide_search_queries`는 답변 한 건마다 최대 5개의 가이드 검색 질의를 순서대로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `position` | `integer NOT NULL` | 분해 LLM이 반환한 배열 순서, `CHECK (position BETWEEN 1 AND 5)` |
| `title` | `varchar(120) NOT NULL` | 고객 응답 소제목 |
| `search_query` | `text NOT NULL` | 독립 검색 가능한 한국어 질의 |
| `evidence` | `text NOT NULL` | 고객 답변의 연속 원문 |

두 테이블은 다음 추적 컬럼을 공유한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `chat_session_id` | `varchar(64) NOT NULL` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `evidence` | `text NOT NULL` | |
| `source_answer_id` | `bigint NULL` FK → `chat_answers.answer_id`, `ON DELETE SET NULL` | 어느 턴의 답변에서 나왔는지 |
| `extracted_at` | `timestamptz NOT NULL` | |

가이드 검색 질의에는 enum이나 코드 컬럼을 두지 않는다. 사기 정황 코드는 enum 추가마다
마이그레이션이 필요하지 않도록 DB CHECK 대신 [3.8](#38-appdomain-enum-코드-상수화)의
파이썬 화이트리스트로 검증한다.

DB에 저장하는 `evidence`는 고객 답변에 실제로 존재하는 연속 문자열인지 대조한다.
가이드 검색 질의는 불일치해도 RAG 입력에서는 유지되지만 리포지토리가 해당 감사 행을
저장하지 않는다. 사기 정황은 추출 서비스가 불일치 항목을 로그로 남기고 버린다.

### 3.7 `fraud_type_score_after_chat`

현재 테이블은 `fraud_type_score_results.type_scores`와 마찬가지로 유형별 점수를 전부
`type_scores`에 남긴다. 대표 유형과 판정 상태는 중복 저장하지 않고 소비자가
`type_scores`에서 계산한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `transaction_id` | `bigint PK` FK → `transactions.id`, `ON DELETE CASCADE` | 거래당 채점 결과 한 행 |
| `chat_session_id` | `varchar(64) NOT NULL` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | 점수를 만든 상담 세션 |
| `type_scores` | `jsonb NOT NULL DEFAULT '{}'` | 4개 유형별 누적 점수 전부 |
| `scored_at` | `timestamptz NOT NULL` | 채점 시각 |

최고점이 0이면 정황 없음, 0보다 큰 최고점을 여러 유형이 공유하면 동점이고, 그 외에는
최고점을 가진 하나가 대표 유형이다. 다만 이 계산은 **백엔드가 하지 않는다.** `rule_scores`와
같은 방침으로 백엔드는 유형별 점수만 남기고, 대표 유형이 필요한 소비자가 `type_scores`에서
직접 계산한다. 백엔드 안에 사용처가 생기면 그때 계산 함수를 추가한다.

**중복 갱신 방지**: `transaction_id`가 PK이므로 거래당 1행이다. 채점은
[2.6](README.md#채점-시점과-중복-방지)대로 상담 종료 시 한 번만 수행하고, 이미 행이 있으면
갱신하지 않는다(`ON CONFLICT DO NOTHING`). 재상담이 생기면 그때 정책을 다시 정한다.

### 3.8 `app/domain/` enum 코드 상수화

동적 가이드 검색 질의는 enum을 사용하지 않는다. `fraud_circumstance` 20종만 코드 상수로
관리하며 다음 세 곳에서 같은 정의를 사용한다.

- LLM 프롬프트에 나열하는 enum 목록과 설명
- 구조화 출력(structured output) 스키마의 허용값
- DB 저장 시 값 검증

세 곳에 각각 문자열을 두면 값 추가·삭제·오타 수정 시 어긋나므로
`app/domain/fraud_circumstance_codes.py`를 단일 출처로 사용한다.

```
app/domain/fraud_circumstance_codes.py
    FRAUD_CIRCUMSTANCE_DESCRIPTIONS: Mapping[str, str]           # 20종
    FRAUD_CIRCUMSTANCE_SCORES: Mapping[str, Mapping[str, int]]   # 내부 채점표 (2.6)
    FINAL_FRAUD_CIRCUMSTANCE_CODES = frozenset(...)
```

`FRAUD_CIRCUMSTANCE_SCORES`가 [내부 채점표](scoring.md#채점표)를 그대로 담는다.
정황 하나가 여러 유형에 점수를 주므로 `Mapping[정황코드, Mapping[사기유형코드, 점수]]`
구조이며, 이 안에 정황 → 사기유형 관계가 포함되어 별도 매핑이 필요 없다.
프롬프트의 "허용된 enum 이외의 값은 생성하지 않습니다" 같은 규칙은 LLM에 대한 요청일 뿐
강제가 아니다. 모델이 이를 어길 가능성은 항상 있으므로 **구조화 출력 스키마와 DB 저장은
파이썬 코드로 강제한다.**

- **구조화 출력 스키마**: 사기 정황 `type`을 `Literal[*FINAL_FRAUD_CIRCUMSTANCE_CODES]`로
  선언해 파서 단계에서 화이트리스트 밖의 값이 들어오면 실패하게 한다.
- **DB 저장**: 파서를 통과한 뒤에도 저장 직전에 정황 코드를 다시 검증한다.
  거짓이면 해당 항목만 저장하지 않고 로그로 남긴다(전체 추출 결과를 폐기하지 않고
  항목 단위로 걸러낸다).

### 3.9 `customers.email` 확보 경로

`customers.email`은 현재 nullable `varchar(255)`다. 거래 API는 기존 고객·계좌 원장을
조회해서 거래를 처리하며 고객을 생성하거나 이메일을 갱신하지 않는다.
`TransactionRequestDTO`에도 `customer_email` 필드가 없고 `extra="forbid"`이므로 거래
요청에 임의로 포함하면 `422`가 된다.

Agent 안내 메일은 출금 계좌에 연결된 고객의 `customers.email`을 사용한다. 값이 없거나
공백이면 `CHAT_FALLBACK_EMAIL`로 보내고, 실제 수신 주소를
`chat_sessions.notified_email`에 저장한다. 따라서 데모는 진행되지만 실제 운영에서는
고객 원장을 적재하는 별도 경로가 이메일을 미리 채워야 한다.

현재 계약의 핵심은 다음과 같다.

- `customers.email`은 nullable로 유지한다.
- `POST /transactions`는 이메일 수집·수정 API가 아니다.
- 이메일이 있으면 고객 주소, 없으면 `CHAT_FALLBACK_EMAIL`을 사용한다.
- 폴백은 데모용이며 실제 고객 주소 확보를 대신하지 않는다.

### 3.10 마이그레이션 적용 순서

현재 대화 스키마는 초기 마이그레이션
`migrations/versions/2026_08_18_1709-6040d304df8d_init_schema.py`에 모두 포함되어 있다.

1. `app/domain/fraud_circumstance_codes.py`에 사기 정황 enum과 채점표를 둔다.
2. [app/data/model/chatbot.py](../../app/data/model/chatbot.py)에 `ChatAnswer`,
   `ChatGuideSearchQuery`, `ChatFraudCircumstance` 추가 +
   `ChatSession` / `FraudTypeScoreAfterChat` 수정.
3. **[app/data/model/\_\_init\_\_.py](../../app/data/model/__init__.py)에 새 모델 import 추가.**
   빠뜨리면 autogenerate가 `DROP TABLE`을 낸다.
4. 이후 스키마 변경은 초기 마이그레이션을 수정하지 않고 새 리비전으로 추가한다.
5. 리포지토리는 가이드 검색 질의를 `(source_answer_id, position)` 기준으로 멱등 저장한다.
6. `customers.email`은 nullable이며 거래 API와 별도의 고객 원장 적재 경로가 채운다.

손대지 않을 것:

- **`transactions`** — 챗봇 전용 컬럼을 추가하지 않는다.
- **`customers`** — `email`은 nullable로 유지한다([3.9](#39-customersemail-확보-경로)).
- **`cs_guide_documents` / `cs_guide_document_chunks`** — 1536차원 + HNSW 코사인 인덱스가
  이미 맞다. `retriever_source`의 **반환 타입만** 구조화한다.
- **`agent_cases` ↔ `chat_sessions` FK** — 둘 다 `transaction_id` UNIQUE라 조인 비용이
  사실상 없다. FK를 새로 걸면 생성 순서 의존이 생기므로 지금은 두지 않는다.

---
