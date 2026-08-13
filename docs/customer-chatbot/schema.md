# 고객 대응 챗봇 — DB·스키마

[고객 대응 챗봇 설계 (PRD)](README.md)의 부속 문서다.
챗봇이 쓰는 테이블·컬럼 정의와 마이그레이션 적용 순서를 담는다.
각 값을 언제 쓰는지는 PRD의 [2. 작동 시나리오](README.md#2-작동-시나리오)에 있다.

| 절 | 내용 |
| --- | --- |
| [3.1](#31-사기-유형) | 사기 유형 코드 |
| [3.2](#32-관련-테이블) | 관련 테이블 목록 |
| [3.3](#33-챗봇-상태-정의) | `ChatSessionStatus` 5종 |
| [3.4](#34-agent_chat_sessions--컬럼-추가) | `agent_chat_sessions` 컬럼 추가 + 대화 진행 상태 |
| [3.5](#35-agent_chat_answers--신규) | `agent_chat_answers` 신규 |
| [3.6](#36-추출-결과-테이블--신규) | 고객 행동·사기 정황 추출 테이블 신규 |
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
| 챗봇 세션·메시지 | `agent_chat_sessions`, `agent_chat_messages` | 기존, 세션에 컬럼 추가 |
| 질문·시도·판정 이력 | `agent_chat_answers` | **신규** |
| 추출 결과 | `agent_chat_customer_actions`, `agent_chat_fraud_circumstances` | **신규** |
| 채팅 후 사기유형 점수 | `fraud_type_score_after_chat` | 기존, 구조 변경 |
| 유저 대응가이드 임베딩 | `cs_guide_documents`, `cs_guide_document_chunks` | 기존, 변경 없음 |

### 3.3 챗봇 상태 정의

`agent_chat_sessions.status` (`ChatSessionStatus`)는 다음 5개 값을 가진다.

| 값 | 의미 |
| --- | --- |
| `URL_SENT` | 챗봇 URL 전송 |
| `IN_PROGRESS` | 챗봇 상담 진행중 |
| `HANDOFF_REQUESTED` | 상담사 연결 요청 |
| `DONE` | 챗봇 상담 완료 |
| `FAILED` | 챗봇 상담 실패 |

별도의 "생성됨" 상태는 두지 않는다. 세션 생성 시 기본값은 `URL_SENT`다.

`FAILED`로 분기하는 로직은 아직 없다. 고객 이메일이 없는 경우는
[2.1의 기본 주소 폴백](README.md#발송-구현과-기본-주소-폴백)으로 처리되므로 실패가 아니다.
실패 분기가 정해지면 사유를 남길 컬럼(`agent_cases.failure_reason` 패턴)을 함께 추가한다.

### 3.4 `agent_chat_sessions` — 컬럼 추가

기존 컬럼(`chat_session_id`, `transaction_id`, `status`, `last_message_id`, `is_older`,
`created_at`)은 그대로 두고 다음을 추가한다. 사용처가 없는 `top_fraud_types`는 삭제한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
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

- 시도 횟수·판정 이력은 [3.5 `agent_chat_answers`](#35-agent_chat_answers--신규)에 영구
  기록되므로, 유실되는 것은 "지금 어느 노드에서 무엇을 기다리는지"뿐이다. 이미 받은
  고객 답변과 판정, 추출 결과는 남는다.
- `question_step`은 `InMemorySaver`의 사본이 아니라 운영 조회용 값이다. 턴이 끝날 때 갱신한다.
- 다중 인스턴스 배포도 고려하지 않는다([2.7의 SSE](README.md#27-상담사-반환-경로-sse)와 같은 전제다).

### 3.5 `agent_chat_answers` — 신규

`agent_chat_messages`는 `sender_type` / `message_text` / `sent_at`만 가진 순수 대화 로그라
다음 정보가 어디에도 남지 않는다.

- 그 답이 몇 번째 시도(재질문 포함)인지
- 평가 LLM 판정 결과(`SUFFICIENT` / `TOO_VAGUE` / `NON_ANSWER` / `REFUSAL` / `WANT_END`)
- 여러 시도 중 실제로 채택되어 추출과 판정에 쓰인 답이 어느 것인지

이 정보가 없으면 추출/판정 LLM의 입력을 사후에 재구성할 수 없어 재현·디버깅·담당자 검토가
전부 불가능하다. `agent_chat_messages`에 컬럼을 얹는 대신 별도 테이블을 둔다.
`agent_chat_messages`는 지금처럼 순수 대화 로그로 남고, `agent_chat_answers`가 그중
"질문에 대한 답변으로 채택(또는 재시도)된 메시지"만 골라 구조화된 메타데이터를 붙인다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `answer_id` | `BIGSERIAL PK` | |
| `chat_session_id` | `varchar(64)` FK → `agent_chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `question_step` | `integer NOT NULL` | 질문 진행 단계 |
| `attempt_no` | `integer NOT NULL` | 해당 질문에 대한 시도 번호. 최초 응답이 1, 조건 1에 따라 최대 3 |
| `message_id` | `bigint NOT NULL` FK → `agent_chat_messages.message_id`, `ON DELETE CASCADE`, `UNIQUE` | 이 시도의 고객 답변 원문이 저장된 메시지 |
| `quality_verdict` | `varchar(16) NULL` | `SUFFICIENT` / `TOO_VAGUE` / `NON_ANSWER` / `REFUSAL` / `WANT_END`. 평가 LLM을 거치지 않았으면 `NULL` |
| `verdict_skip_reason` | `varchar(24) NULL` | `quality_verdict`가 `NULL`인 원인. `MAX_RETRY_EXCEEDED` / `EVALUATOR_FAILED` |
| `is_adopted` | `boolean NOT NULL DEFAULT false` | 이 시도가 최종 채택되어 추출 입력에 쓰였는지. 질문당 정확히 하나만 `true` |
| `created_at` | `timestamptz NOT NULL` | |

제약:

```sql
UNIQUE (chat_session_id, question_step, attempt_no)
CHECK (attempt_no BETWEEN 1 AND 3)
CHECK (quality_verdict IS NULL
       OR quality_verdict IN ('SUFFICIENT','TOO_VAGUE','NON_ANSWER','REFUSAL','WANT_END'))
CHECK (verdict_skip_reason IS NULL
       OR verdict_skip_reason IN ('MAX_RETRY_EXCEEDED','EVALUATOR_FAILED'))

CREATE INDEX ix_agent_chat_answers_session_step
    ON agent_chat_answers (chat_session_id, question_step);

-- 질문당 채택 답변은 최대 하나
CREATE UNIQUE INDEX uq_agent_chat_answers_adopted
    ON agent_chat_answers (chat_session_id, question_step) WHERE is_adopted;
```

부분 유니크 인덱스는 채택 답변이 **둘 이상 생기지 않는 것**만 강제한다. 답변을 채택하는
턴에서는 파이프라인이 반드시 하나를 `is_adopted = true`로 저장해 최소 한 개 규칙을 지킨다.

`verdict_skip_reason`이 없으면 "재시도 초과로 평가 없이 pass"와 "평가 LLM 장애"가 똑같이
`NULL`로 보여 구분할 수 없다. 로그는 지워지므로 컬럼으로 남긴다.

### 3.6 추출 결과 테이블 — 신규

고객 행동은 RAG 검색에, 사기 정황은 점수 집계에 사용되므로 별도 테이블에 저장한다.
두 테이블은 `kind` 구분자 없이 각 도메인의 코드 컬럼을 명시적으로 가진다.

| 테이블 | PK | 코드 컬럼 | 세션별 중복 방지 |
| --- | --- | --- | --- |
| `agent_chat_customer_actions` | `action_id BIGSERIAL` | `action_code varchar(64)` | `UNIQUE (chat_session_id, action_code)` |
| `agent_chat_fraud_circumstances` | `circumstance_id BIGSERIAL` | `circumstance_code varchar(64)` | `UNIQUE (chat_session_id, circumstance_code)` |

두 테이블의 공통 컬럼은 다음과 같다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `chat_session_id` | `varchar(64)` FK → `agent_chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `evidence` | `text NOT NULL` | |
| `evidence_verified` | `boolean NOT NULL` | 고객 답변 원문 대조 통과 여부 |
| `source_answer_id` | `bigint NULL` FK → `agent_chat_answers.answer_id`, `ON DELETE SET NULL` | 어느 턴의 답변에서 나왔는지 |
| `extracted_at` | `timestamptz NOT NULL` | |

코드 컬럼에 19종·20종 CHECK를 걸지 않는다. enum 하나 추가할 때마다 마이그레이션이
필요하고, 이 값들은 프롬프트 튜닝과 함께 자주 바뀐다. [3.8](#38-appdomain-enum-코드-상수화)의
파이썬 화이트리스트를 1차 방어선으로 둔다(구조화 출력 스키마 + 저장 직전 검증).
`fraud_type_score_results.type_scores`가 유형 코드를 JSON으로 담고 CHECK 없이 코드로
관리하는 것과 같은 선택이다.

`evidence_verified`는 프롬프트의 "evidence는 사용자 답변에 실제로 존재하는 연속된 원문
문자열이어야 합니다" 규칙이 지켜졌는지를 저장 직전에 대조한 결과다. 이 규칙은 LLM에 대한
요청일 뿐 강제가 아니므로, 대조에 실패한 항목은 `false`로 저장해 담당자가 걸러낼 수 있게 한다.

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

최고점이 0이면 정황 없음, 0보다 큰 최고점을 여러 유형이 공유하면 동점으로 판정한다.
그 외에는 최고점을 가진 하나의 유형을 대표 유형으로 계산한다.

**중복 갱신 방지**: `transaction_id`가 PK이므로 거래당 1행이다. 채점은
[2.6](README.md#채점-시점과-중복-방지)대로 상담 종료 시 한 번만 수행하고, 이미 행이 있으면
갱신하지 않는다(`ON CONFLICT DO NOTHING`). 재상담이 생기면 그때 정책을 다시 정한다.

### 3.8 `app/domain/` enum 코드 상수화

`customer_action` 19종과 `fraud_circumstance` 20종이 현재 프롬프트 문자열 안에만
나열되어 있고 코드 상수로 정의된 곳이 없다. 이 값들은 최소 세 곳에서 쓰인다.

- LLM 프롬프트에 나열하는 enum 목록과 설명
- 구조화 출력(structured output) 스키마의 허용값
- DB 저장 시 값 검증

세 곳에 각각 문자열로 박아두면 값 추가·삭제·오타 수정 시 반드시 어긋난다.
`app/domain/fraud_type_codes.py`가 사기유형 코드에 대해 이미 하는 패턴을 그대로 적용한다.

```
app/domain/customer_action_codes.py
    CUSTOMER_ACTION_DESCRIPTIONS: Mapping[str, str]    # 프롬프트 렌더링용 19종
    CUSTOMER_ACTION_SEARCH_QUERIES: Mapping[str, str]  # RAG 검색 질의용 한국어 매핑
    FINAL_CUSTOMER_ACTION_CODES = frozenset(...)

app/domain/fraud_circumstance_codes.py
    FRAUD_CIRCUMSTANCE_DESCRIPTIONS: Mapping[str, str]           # 20종
    FRAUD_CIRCUMSTANCE_SCORES: Mapping[str, Mapping[str, int]]   # 내부 채점표 (2.6)
    FINAL_FRAUD_CIRCUMSTANCE_CODES = frozenset(...)
```

`FRAUD_CIRCUMSTANCE_SCORES`가 [내부 채점표](scoring.md#채점표)를 그대로 담는다.
정황 하나가 여러 유형에 점수를 주므로 `Mapping[정황코드, Mapping[사기유형코드, 점수]]`
구조이며, 이 안에 정황 → 사기유형 관계가 포함되어 별도 매핑이 필요 없다.
검색 질의는 코드 문자열 대신 아래 한국어 문구를 사용한다. 행동 설명보다 검색 의도를
분명히 하기 위해 모든 문구에 피해 대응 맥락을 포함한다.

| `customer_action` | `CUSTOMER_ACTION_SEARCH_QUERIES` |
| --- | --- |
| `detected_transaction_initiated` | 의심 거래를 직접 입력하고 실행했을 때 대응 방법 |
| `detected_transaction_approved` | 다른 사람이 준비한 의심 거래를 인증하거나 승인했을 때 대응 방법 |
| `cash_delivered_after_withdrawal` | 현금을 출금해 다른 사람에게 직접 전달했을 때 대응 방법 |
| `received_funds_forwarded` | 입금받은 돈을 다른 계좌나 사람에게 다시 송금했을 때 대응 방법 |
| `received_funds_withdrawn` | 입금받은 돈을 현금으로 출금했을 때 대응 방법 |
| `goods_or_asset_delivered_for_payment` | 입금 대가로 물품·금·외화 등 자산을 전달했을 때 대응 방법 |
| `bank_account_rented_or_transferred` | 본인 명의 계좌를 다른 사람에게 대여하거나 양도했을 때 대응 방법 |
| `account_access_or_payment_instrument_shared` | 금융계정 접근정보·통장·카드·OTP 기기를 전달했을 때 대응 방법 |
| `phishing_link_opened` | 상대방이 보낸 의심스러운 링크를 열거나 눌렀을 때 대응 방법 |
| `financial_credentials_entered_or_shared` | 금융서비스 아이디·비밀번호·PIN을 입력하거나 전달했을 때 대응 방법 |
| `otp_or_authentication_code_shared` | OTP·문자·ARS 인증번호를 입력하거나 전달했을 때 대응 방법 |
| `identity_document_shared` | 신분증 사진·사본·위임장을 전달했을 때 대응 방법 |
| `card_information_shared` | 카드번호·유효기간·CVC·카드 비밀번호를 전달했을 때 대응 방법 |
| `suspicious_app_installed` | 상대방이 안내한 앱이나 APK를 설치했을 때 대응 방법 |
| `remote_control_or_security_permission_granted` | 원격제어·접근성·기기관리자 권한을 허용했을 때 대응 방법 |
| `loan_taken_for_transaction` | 의심 거래 자금을 마련하려고 대출을 실행했을 때 대응 방법 |
| `account_opened_for_other_party` | 상대방 요청으로 계좌를 개설하거나 사용하게 했을 때 대응 방법 |
| `open_banking_or_external_finance_linked` | 상대방 요청으로 오픈뱅킹이나 외부 금융서비스를 연결했을 때 대응 방법 |
| `crypto_purchased_or_transferred` | 의심 거래와 관련해 가상자산을 구매하거나 외부 지갑으로 전송했을 때 대응 방법 |

프롬프트의 "허용된 enum 이외의 값은 생성하지 않습니다" 같은 규칙은 LLM에 대한 요청일 뿐
강제가 아니다. 모델이 이를 어길 가능성은 항상 있으므로 **구조화 출력 스키마와 DB 저장은
파이썬 코드로 강제한다.**

- **구조화 출력 스키마**: `type` 필드를 `Enum` 또는 `Literal[*FINAL_CUSTOMER_ACTION_CODES]`로
  선언해 파서 단계에서 화이트리스트 밖의 값이 들어오면 실패하게 한다.
- **DB 저장**: 파서를 통과한 뒤에도 저장 직전에 `code in FINAL_*_CODES`를 다시 검증한다.
  거짓이면 해당 항목만 저장하지 않고 로그로 남긴다(전체 추출 결과를 폐기하지 않고
  항목 단위로 걸러낸다).

### 3.9 `customers.email` 확보 경로

챗봇의 진입점은 이메일이므로 `customers.email`이 비어 있으면 기능 전체가 시작되지 않는다.
현재 코드에서 이 값은 **어떤 경로로도 채워지지 않는다.**

- ML 54개 입력 계약에 이메일 컬럼이 없고,
  `build_customer_fields`([ml_feature_assembler.py:75-84](../../app/services/features/ml_feature_assembler.py#L75-L84))도
  `birthyear` / `gender` / `registration_datetime` / `credit_rating` / `loan_type`만 뽑는다.
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

1. `app/domain/customer_action_codes.py`, `app/domain/fraud_circumstance_codes.py` —
   나머지가 전부 여기 의존한다.
2. [app/data/model/chatbot.py](../../app/data/model/chatbot.py)에 `AgentChatAnswer`,
   `AgentChatCustomerAction`, `AgentChatFraudCircumstance` 추가 +
   `AgentChatSession` / `FraudTypeScoreAfterChat` 수정.
3. **[app/data/model/\_\_init\_\_.py](../../app/data/model/__init__.py)에 새 모델 import 추가.**
   빠뜨리면 autogenerate가 `DROP TABLE`을 낸다.
4. `uv run --env-file .env alembic revision --autogenerate` →
   부분 유니크 인덱스(`WHERE is_adopted`)는 autogenerate가 잡지 못하므로 손으로 넣는다.
   이미 병합된 마이그레이션은 수정하지 말고 새 리비전을 쌓는다.
5. `app/repositories/chat_session.py` 신규 — 현재 챗봇 리포지토리가 없다.
6. [3.9](#39-customersemail-확보-경로)의 `customer_email` 필드 추가 — 마이그레이션 없음.

손대지 않을 것:

- **`transactions`** — ML 54개 입력 계약이 걸려 있어 컬럼을 추가하지 않는다.
- **`customers`** — `email`은 nullable로 두고 채우는 경로만 만든다([3.9](#39-customersemail-확보-경로)).
- **`cs_guide_documents` / `cs_guide_document_chunks`** — 1536차원 + HNSW 코사인 인덱스가
  이미 맞다. `retriever_source`의 **반환 타입만** 구조화한다.
- **`agent_cases` ↔ `agent_chat_sessions` FK** — 둘 다 `transaction_id` UNIQUE라 조인 비용이
  사실상 없다. FK를 새로 걸면 생성 순서 의존이 생기므로 지금은 두지 않는다.

---
