# 고객 대응 챗봇 — DB·스키마

[고객 대응 챗봇 설계 (PRD)](README.md)의 부속 문서다.
챗봇이 쓰는 테이블·컬럼 정의와 마이그레이션 적용 순서를 담는다.
각 값을 언제 쓰는지는 PRD의 [2. 작동 시나리오](README.md#2-작동-시나리오)에 있다.
전체 테이블 관계는 [Mermaid ERD](erd.md)에서 확인할 수 있다.

## 구현 상태

**챗봇 영속 스키마 구현 완료 (2026-08-15).**

- `fraud_circumstance` 20종과 유형별 점수표를 `app/domain/`에 정의했다.
- [app/data/model/chatbot.py](../../app/data/model/chatbot.py)에 세션·메시지·답변·가이드 검색 질의·
  사기 정황·채점 결과 모델을 정의하고
  [app/data/model/__init__.py](../../app/data/model/__init__.py)에 등록했다.
- Alembic revision `c4f7a2b9d810`에 기존 `agent_chat_*` 테이블 이름 변경, 신규 테이블 생성,
  채점 결과 백필, FK·CHECK·UNIQUE·부분 유니크 인덱스 적용과 downgrade를 구현했다.
- Alembic revision `d94b7e31a5c2`가 [유형판별 질문](README.md#유형판별-질문) 도입으로
  사용처가 생긴 `chat_sessions.top_fraud_types`를 재추가했다 (2026-08-15).
- Alembic revision `f8a1b2c3d4e5`가 `chat_customer_actions`를 제거하고
  `chat_guide_search_queries`로 교체했다. 기존 고객행동 행은 백필하지 않는다.

이 완료 표시는 이 문서의 챗봇 영속 구조(3.1~3.8)에 한정한다. 리포지토리·DTO·LLM·API와
[3.9의 이메일 확보 경로](#39-customersemail-확보-경로)는 후속 애플리케이션 작업이다.

| 절 | 내용 |
| --- | --- |
| [구현 상태](#구현-상태) | 챗봇 영속 스키마 완료 범위 |
| [3.1](#31-사기-유형) | 사기 유형 코드 |
| [3.2](#32-관련-테이블) | 관련 테이블 목록 |
| [3.3](#33-챗봇-상태-정의) | `ChatSessionStatus` 5종 |
| [3.4](#34-chat_sessions--테이블명-변경-및-컬럼-추가) | `chat_sessions` 테이블명 변경 + 컬럼 추가 |
| [3.5](#35-chat_answers--신규) | `chat_answers` 신규 |
| [3.6](#36-추출-결과-테이블--신규) | 가이드 검색 질의·사기 정황 추출 테이블 |
| [3.7](#37-fraud_type_score_after_chat--구조-변경) | `fraud_type_score_after_chat` 구조 변경 |
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

| 구분 | 테이블 | 상태 |
| --- | --- | --- |
| 거래 원장 | `transactions` | 기존, 변경 없음 |
| 챗봇 세션·메시지 | `chat_sessions`, `chat_messages` | 기존 테이블명 변경, 세션에 컬럼 추가 |
| 질문·시도·판정 이력 | `chat_answers` | **신규** |
| 추출 결과 | `chat_guide_search_queries`, `chat_fraud_circumstances` | **신규** |
| 채팅 후 사기유형 점수 | `fraud_type_score_after_chat` | 기존, 구조 변경 |
| 유저 대응가이드 임베딩 | `cs_guide_documents`, `cs_guide_document_chunks` | 기존, 변경 없음 |

### 3.3 챗봇 상태 정의

`chat_sessions.status` (`ChatSessionStatus`)는 다음 5개 값을 가진다.

| 값 | 의미 |
| --- | --- |
| `URL_SENT` | 챗봇 URL 전송 |
| `IN_PROGRESS` | 챗봇 상담 진행중 |
| `HANDOFF_REQUESTED` | 상담사 연결 요청 |
| `DONE` | 챗봇 상담 완료 |
| `FAILED` | 챗봇 상담 실패 |

별도의 "생성됨" 상태는 두지 않는다. 세션 생성 시 기본값은 `URL_SENT`다.

`FAILED`로 분기하는 경로는 **접속 URL 메일 발송 실패 하나**다
([2.1 발송 구현](README.md#발송-구현과-기본-주소-폴백)). 고객이 URL을 받지 못해 챗봇이 시작될 수
없는 상태이므로 `URL_SENT`로 둘 수 없다. 고객 이메일이 없는 경우는 같은 절의 기본 주소
폴백으로 처리되므로 실패가 아니다.

실패 사유를 남길 컬럼(`agent_cases.failure_reason` 패턴)은 아직 두지 않았다. 지금은 분기가
하나뿐이라 `status = FAILED` + `email_sent_at IS NULL`로 사유가 특정되고, 예외 상세는 로그에
남는다. 다른 실패 분기가 생기면 그때 컬럼을 추가한다.

### 3.4 `chat_sessions` — 테이블명 변경 및 컬럼 추가

기존 `agent_chat_sessions`와 `agent_chat_messages`는 데이터를 유지한 채 각각
`chat_sessions`, `chat_messages`로 이름을 변경한다. 관련 제약조건·인덱스·메시지 ID
시퀀스에서도 `agent_` 접두사를 제거한다.

기존 컬럼(`chat_session_id`, `transaction_id`, `status`, `last_message_id`, `is_older`,
`created_at`)은 그대로 두고 다음을 추가한다.

`top_fraud_types`는 `c4f7a2b9d810`이 사용처 없음을 이유로 삭제했으나,
[2.4 유형판별 질문](README.md#유형판별-질문) 도입으로 사용처가 생겨 `d94b7e31a5c2`에서
같은 형태(`jsonb NULL`)로 재추가한다. 세션 생성 시점([2.1](README.md#21-채팅-세션-생성-및-이메일-전송))과
고객이 "챗봇 상담" 버튼을 누르는 시점 사이에 값이 살아 있어야 하므로 DB에 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `top_fraud_types` | `jsonb NULL` | 룰 채점 점수 내림차순 상위 2개 사기유형 코드([3.1](#31-사기-유형)). [유형판별 질문](README.md#유형판별-질문) 선택에 쓰고, `NULL`이면 일반 질문 폴백 |
| `question_step` | `integer NOT NULL DEFAULT 0` | 현재 질문 단계. 담당자 화면에서 "이 세션이 몇 번 질문에서 멈춰 있는지"를 세션 목록 조회 한 번으로 보기 위한 값 |
| `email_sent_at` | `timestamptz NULL` | 챗봇 URL 메일을 보낸 시각 |
| `notified_email` | `varchar(255) NULL` | 실제로 보낸 수신 주소. 기본 주소 폴백이 있어 `customers.email`과 다를 수 있으므로 보낸 값을 그대로 남긴다 |
| `completed_at` | `timestamptz NULL` | 종료 시각 |

#### 대화 진행 상태

정보 수집 단계는 LangGraph로 구현되는 상태 기계다(현재 질문이 무엇인지, 조건 1의 재시도
횟수, 평가 결과 대기 중인지 등).

**LangGraph 체크포인터는 `InMemorySaver`를 쓴다.** 진행 상태는 `chat_session_id`를
`thread_id`로 삼아 프로세스 메모리에만 둔다. **서버 재시작·재배포는 고려하지 않는다.**
진행 중이던 세션이 유실되는 것은 MVP에서 감수하는 트레이드오프이며, 진행 상태를 DB에
스냅샷으로 저장하는 컬럼은 두지 않는다.

- 시도 횟수·판정 이력은 [3.5 `chat_answers`](#35-chat_answers--신규)에 영구
  기록된다. 체크포인터의 현재 질문 재시도 횟수는 초기화되지만, 이미 받은 고객 답변과
  판정, 추출 결과는 남는다.
- `question_step`은 `InMemorySaver`의 사본이 아니라 운영 조회용 값이다. 턴이 끝날 때 갱신한다.
  체크포인트가 없는 첫 턴과 재시작 이후에는 이 컬럼이 그래프 상태의 seed가 되므로,
  유실되는 것은 재시도 횟수뿐이다. 답변 수신 가능 여부는 세션 상태와 이 컬럼으로 검증한다
  ([customer_chatbot_pipeline.py](../../app/pipelines/customer_chatbot_pipeline.py)).
- 메모리 체크포인터를 공유할 수 없는 다중 인스턴스 배포도 고려하지 않는다.

### 3.5 `chat_answers` — 신규

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
| `chat_session_id` | `varchar(64)` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `question_step` | `integer NOT NULL` | 질문 진행 단계 |
| `attempt_no` | `integer NOT NULL` | 해당 질문에 대한 시도 번호. 최초 응답이 1, 조건 1에 따라 최대 3 |
| `message_id` | `bigint NOT NULL` FK → `chat_messages.message_id`, `ON DELETE CASCADE`, `UNIQUE` | 이 시도의 고객 답변 원문이 저장된 메시지 |
| `quality_verdict` | `varchar(16) NULL` | `SUFFICIENT` / `TOO_VAGUE` / `WANT_END`. 평가 LLM을 거치지 못했으면 `NULL` |
| `verdict_skip_reason` | `varchar(24) NULL` | `quality_verdict`가 `NULL`인 원인. `MAX_RETRY_EXCEEDED` / `EVALUATOR_FAILED` |
| `is_adopted` | `boolean NOT NULL DEFAULT false` | 이 시도가 최종 채택되어 추출 입력에 쓰였는지. 질문당 정확히 하나만 `true` |
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

부분 유니크 인덱스는 채택 답변이 **둘 이상 생기지 않는 것**만 강제한다. 답변을 채택하는
턴에서는 파이프라인이 반드시 하나를 `is_adopted = true`로 저장해 최소 한 개 규칙을 지킨다.

`verdict_skip_reason`이 없으면 "재시도 초과로 평가 없이 pass"와 "평가 LLM 장애"가 똑같이
`NULL`로 보여 구분할 수 없다. 로그는 지워지므로 컬럼으로 남긴다.

### 3.6 추출 결과 테이블 — 신규

동적 가이드 검색 질의는 RAG 검색과 감사 기록에, 사기 정황은 점수 집계에 사용한다.

| 테이블 | PK | 핵심 컬럼 | 중복 방지 |
| --- | --- | --- | --- |
| `chat_guide_search_queries` | `guide_search_query_id BIGSERIAL` | `position`, `title`, `search_query` | `UNIQUE (source_answer_id, position)` |
| `chat_fraud_circumstances` | `circumstance_id BIGSERIAL` | `circumstance_code varchar(64)` | `UNIQUE (chat_session_id, circumstance_code)` |

`chat_guide_search_queries`는 답변 한 건마다 최대 5개의 가이드 검색 질의를 순서대로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `position` | `integer NOT NULL` | 원문 언급 순서, `CHECK (position BETWEEN 1 AND 5)` |
| `title` | `varchar(120) NOT NULL` | 고객 응답 소제목 |
| `search_query` | `text NOT NULL` | 독립 검색 가능한 한국어 질의 |
| `evidence` | `text NOT NULL` | 고객 답변의 연속 원문 |

두 테이블은 다음 추적 컬럼을 공유한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `chat_session_id` | `varchar(64)` FK → `chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `evidence` | `text NOT NULL` | |
| `source_answer_id` | `bigint NULL` FK → `chat_answers.answer_id`, `ON DELETE SET NULL` | 어느 턴의 답변에서 나왔는지 |
| `extracted_at` | `timestamptz NOT NULL` | |

가이드 검색 질의에는 enum이나 코드 컬럼을 두지 않는다. 사기 정황 코드는 enum 추가마다
마이그레이션이 필요하지 않도록 DB CHECK 대신 [3.8](#38-appdomain-enum-코드-상수화)의
파이썬 화이트리스트로 검증한다.

`evidence`는 저장 직전에 고객 답변에 실제로 존재하는 연속된 원문 문자열인지 대조한다.
대조에 실패한 추출 항목은 로그를 남기고 저장하지 않는다.

### 3.7 `fraud_type_score_after_chat` — 구조 변경

유형별 점수를 전부 남기는 구조로 바꾼다. `fraud_type_score_results.type_scores`가
`dict[str, float]`로 전부 남기는 것과 대칭이 된다. 대표 유형과 판정 상태는 중복 저장하지
않고 `type_scores`에서 계산한다.

| 컬럼 | 변경 | 설명 |
| --- | --- | --- |
| `transaction_id` | 유지 | PK |
| `chat_session_id` | **추가** `varchar(64)` FK, `ON DELETE CASCADE` | 어느 세션의 결과인지 |
| `type_scores` | **추가** `jsonb NOT NULL DEFAULT '{}'` | 4개 유형별 누적 점수 전부 |
| `scored_at` | 유지 | |

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

챗봇의 진입점은 이메일이므로 `customers.email`이 비어 있으면 기능 전체가 시작되지 않는다.
현재 코드에서 이 값은 **어떤 경로로도 채워지지 않는다.**

- ML 54개 입력 계약에 이메일 컬럼이 없고,
  `build_customer_fields`([ml_feature_assembler.py:75-84](../../app/services/features/ml_feature_assembler.py#L75-L84))도
  `birth_date` / `gender` / `registration_datetime` / `credit_rating` / `loan_type`만 뽑는다.
- 마이그레이션 `b21f6a97c4d1`이 "Allow contacts omitted by the transaction CSV contract"라는
  이유로 `email` / `phone_number`를 nullable로 바꿨다.
- `Customer.email`에 값을 쓰는 곳은 [app/data/fake_data.py](../../app/data/fake_data.py)뿐이고,
  `test-data-injection/data/transactions_v5_1000.csv`에도 이메일 컬럼이 없다.

따라서 `POST /transactions`로 만들어지는 고객은 **예외 없이 `email = NULL`** 이다.
[2.1의 기본 주소 폴백](README.md#발송-구현과-기본-주소-폴백)이 있어 챗봇이 멈추지는 않지만, 그대로 두면
**모든 안내 메일이 실제 고객이 아니라 `abcd@kosa.com` 한 곳으로만 간다.** 폴백은 데모가
돌아가게 하는 임시 조치일 뿐이므로 실제 주소가 들어올 경로가 따로 필요하다.

#### 설계

**컬럼은 nullable로 유지한다.** 이미 `NULL`인 행이 있고 CSV 계약이 이메일을 주지 않으므로
`NOT NULL`로 승격할 수 없다. 대신 값이 들어올 경로를 만든다.

`TransactionCreateDTO`에 선택 필드 `customer_email`을 추가하고, `_upsert_customer`가
`customers.email`에 반영한다. 이 DTO는 이미 54개 Feature 바깥의 식별 정보
(`customer_personal_identifier`, `customer_identification_number`, `source_account_number`)를
같은 방식으로 받고 있고, `recipient_account_number`가 선택 필드의 선례다.

```python
customer_email: str | None = Field(
    default=None,
    max_length=255,
    validation_alias=AliasChoices("customer_email", "Customer_email"),
)
```

- **ML 계약은 건드리지 않는다.** `customer_email`은 `raw_data`가 아니라 DTO 레벨 필드이므로
  ML Serving `/predict`로 넘어가는 54개 Feature에 포함되지 않는다.
- **제공될 때만 갱신한다.** 기존 고객의 갱신 경로(`latest_customer_fields`)에서
  `customer_email`이 `None`이면 기존 값을 덮어쓰지 않는다. 이메일을 싣지 않은 후속 거래가
  이미 확보한 주소를 지우면 안 된다.
- **필수로 만들지 않는다.** `extra="forbid"`라 기존 요청은 이 필드 없이도 그대로 통과해야 한다.

이 변경은 챗봇 영역이 아니라 거래 수집 경로(`app/dto/transaction.py`,
`app/repositories/transaction.py`)를 건드린다. 컬럼 타입 변경이 아니므로
마이그레이션은 필요 없다.

### 3.10 마이그레이션 적용 순서

기본 대화 스키마는 `c4f7a2b9d810`, 유형판별 질문 컬럼은 `d94b7e31a5c2`, 가이드 검색 질의
교체는 `f8a1b2c3d4e5`에 반영됐다. `a6b8c9d0e1f2`는 기존 두 head를 병합하면서
답변 평가 판정 CHECK를 3종으로 축소한다.

1. `app/domain/fraud_circumstance_codes.py`에 사기 정황 enum과 채점표를 둔다.
2. [app/data/model/chatbot.py](../../app/data/model/chatbot.py)에 `ChatAnswer`,
   `ChatGuideSearchQuery`, `ChatFraudCircumstance` 추가 +
   `ChatSession` / `FraudTypeScoreAfterChat` 수정.
3. **[app/data/model/\_\_init\_\_.py](../../app/data/model/__init__.py)에 새 모델 import 추가.**
   빠뜨리면 autogenerate가 `DROP TABLE`을 낸다.
4. 기존 마이그레이션은 수정하지 않고 새 리비전을 쌓는다. `a6b8c9d0e1f2`가
   `f3a6c8d2e941`과 `f8a1b2c3d4e5`를 병합하고 `quality_verdict` CHECK를 교체한다.
5. 리포지토리는 가이드 검색 질의를 `(source_answer_id, position)` 기준으로 멱등 저장한다.
6. [3.9](#39-customersemail-확보-경로)의 `customer_email` 필드 추가는 마이그레이션이 없다.

손대지 않을 것:

- **`transactions`** — ML 54개 입력 계약이 걸려 있어 컬럼을 추가하지 않는다.
- **`customers`** — `email`은 nullable로 두고 채우는 경로만 만든다([3.9](#39-customersemail-확보-경로)).
- **`cs_guide_documents` / `cs_guide_document_chunks`** — 1536차원 + HNSW 코사인 인덱스가
  이미 맞다. `retriever_source`의 **반환 타입만** 구조화한다.
- **`agent_cases` ↔ `chat_sessions` FK** — 둘 다 `transaction_id` UNIQUE라 조인 비용이
  사실상 없다. FK를 새로 걸면 생성 순서 의존이 생기므로 지금은 두지 않는다.

---
