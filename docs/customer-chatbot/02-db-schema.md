# 고객 대응 챗봇 설계 — DB·스키마

문서 색인은 [`01-overview.md`](01-overview.md)를 참고한다.

## 3. 공통 스키마 정의

### 3.1 사기 유형

```python
FRAUD_TYPE_DISPLAY_NAMES: Mapping[str, str] = {
    VOICE_PHISHING: "보이스피싱",
    MESSENGER_PHISHING: "메신저피싱",
    ACCOUNT_TAKEOVER: "계정탈취",
    FRAUD_USED_ACCOUNT: "사기이용계좌",
}
```

### 3.2 관련 테이블

| 구분 | 테이블 |
| --- | --- |
| 거래 원장 | `transactions` |
| 챗봇 | `agent_chat_sessions`, `agent_chat_messages` |
| 채팅 후 사기유형 추가 점수 | `fraud_type_score_after_chat` |
| 유저 대응가이드 임베딩 | `cs_guide_documents`, `cs_guide_document_chunks` |

### 3.3 챗봇 상태 정의

`agent_chat_sessions.status` (`ChatSessionStatus`)는 다음 5개 값을 가진다.

| 값 | 의미 |
| --- | --- |
| `URL_SENT` | 챗봇URL전송 |
| `IN_PROGRESS` | 챗봇 상담 진행중 |
| `HANDOFF_REQUESTED` | 상담사 연결 요청 |
| `DONE` | 챗봇 상담 완료 |
| `FAILED` | 챗봇 상담 실패 |

별도의 "생성됨" 상태는 두지 않는다. 세션 생성 시 기본값은 `URL_SENT`다.
챗봇 상담 실패로 분기하는 로직은 아직 만들지 않았다, 추후 구체화 되면 추가 예정

## 7. 추가할 테이블

### 7.1 배경

`agent_chat_messages`([app/data/model/agent.py:254-289](../../app/data/model/agent.py#L254-L289))는
`sender_type` / `message_text` / `sent_at`만 가진 순수 대화 로그다. 이 구조만으로는
[4.2 정보 수집 단계](03-scenario.md#42-정보-수집-단계-챗봇-로직)에서 만들어지는 다음 정보가 어디에도 남지 않는다.

- 어떤 고객 메시지가 **몇 번 질문**(공통질문 1~4, 유형 판별 질문)에 대한 답인지
- 그 답이 **몇 번째 시도**([질문이 완전한가](03-scenario.md#질문이-완전한가)의 재질문 포함)인지
- [질문이 완전한가](03-scenario.md#질문이-완전한가)의 **평가 LLM 판정 결과**(`SUFFICIENT` / `TOO_VAGUE` / `NON_ANSWER` / `REFUSAL`)
- 여러 시도 중 실제로 **고객응답 N**으로 채택되어 [4.2-a](03-scenario.md#42-a-고객-행동-추출)/[4.2-b](03-scenario.md#42-b-사기-정황-추출) 추출과
  [4.3-b](03-scenario.md#43-b-fraud_circumstances-사기유형-추가-판정-과정) 판정에 쓰인 답이 어느 것인지

이 정보가 없으면 추출/판정 LLM의 입력을 사후에 재구성할 수 없어 재현, 디버깅, 담당자 검토가
전부 불가능하다. `agent_chat_messages`에 컬럼을 얹는 대신, 질문·시도·판정 단위를 명확히 표현할 수 있는
별도 테이블 `agent_chat_answers`를 둔다. `agent_chat_messages`는 지금처럼 순수 대화 로그로 남기고,
`agent_chat_answers`가 그중 "질문에 대한 답변으로 채택(또는 재시도)된 메시지"만 골라 구조화된 메타데이터를 붙인다.

### 7.2 `agent_chat_answers`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `answer_id` | `BIGSERIAL PK` | |
| `chat_session_id` | `varchar(64)` FK → `agent_chat_sessions.chat_session_id`, `ON DELETE CASCADE` | |
| `question_key` | `varchar(32)` | `COMMON_1` / `COMMON_2` / `COMMON_3` / `COMMON_4` / `TYPE_DISCRIMINATION` (고객응답 1~5에 대응) |
| `attempt_no` | `integer` | 해당 질문에 대한 시도 번호. 최초 응답이 1, [질문이 완전한가](03-scenario.md#질문이-완전한가)의 조건 1(재질문 최대 1회)에 따라 최대 2 |
| `message_id` | `bigint` FK → `agent_chat_messages.message_id`, `ON DELETE CASCADE` | 이 시도의 고객 답변 원문이 실제로 저장된 메시지 |
| `quality_verdict` | `varchar(16)`, nullable | `SUFFICIENT` / `TOO_VAGUE` / `NON_ANSWER` / `REFUSAL`. 평가 LLM을 거치지 않은 경우(`TYPE_DISCRIMINATION`, 또는 조건 1의 재시도 횟수 초과로 평가 없이 pass된 경우) `NULL` |
| `is_adopted` | `boolean` | 이 시도가 최종적으로 "고객응답 N"으로 채택되어 [4.2-a](03-scenario.md#42-a-고객-행동-추출)/[4.2-b](03-scenario.md#42-b-사기-정황-추출) 추출 입력에 쓰였는지. 질문당 정확히 하나만 `true` |
| `created_at` | `timestamptz` | |

제약조건:

- `UNIQUE (chat_session_id, question_key, attempt_no)` — 같은 질문·같은 시도 번호가 중복 저장되지 않도록 함
- `INDEX (chat_session_id, question_key)` — 세션의 질문별 시도 이력 조회용
- `CHECK (question_key IN ('COMMON_1', 'COMMON_2', 'COMMON_3', 'COMMON_4', 'TYPE_DISCRIMINATION'))`
- `CHECK (quality_verdict IS NULL OR quality_verdict IN ('SUFFICIENT', 'TOO_VAGUE', 'NON_ANSWER', 'REFUSAL'))`

**고객 응답 리스트 구성**: [4.2 유형 판별 질문](03-scenario.md#유형-판별-질문) 이후 "고객응답 1+2+3+4+5 ⇒ 고객 응답 리스트로 병합"
단계는 `agent_chat_answers`에서 `chat_session_id`로 조회한 뒤 `question_key`별로 `is_adopted = true`인
행 하나씩을 골라, 그 `message_id`가 가리키는 `agent_chat_messages.message_text`를 모으는 방식으로 구현한다.

### 7.3 대화 진행 상태(LangGraph state) 영속화

#### 배경

[4.2 정보 수집 단계](03-scenario.md#42-정보-수집-단계-챗봇-로직)는 LangGraph로 구현되는 상태 기계다
(현재 질문이 무엇인지, 조건 1의 재시도 횟수, [질문이 완전한가](03-scenario.md#질문이-완전한가) 평가 결과 대기 중인지 등).
이 진행 상태를 어디에 영속화할지에 대한 계획이 현재 없다. 프로세스 메모리에만 두면
서버 재시작·재배포·다중 인스턴스 환경에서 세션이 끊기거나 유실된다. 챗봇은 이메일 URL로
접속해 시간차를 두고 이어가는 대화이므로, 진행 상태는 반드시 DB에 있어야 다음 메시지가
왔을 때 올바른 노드에서 재개할 수 있다.

#### 설계

`agent_chat_sessions`에 LangGraph state를 담는 JSON 컬럼을 추가한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `graph_state` | `jsonb`, nullable | LangGraph 체크포인터가 직렬화한 대화 진행 상태(JSON) 스냅샷. 현재 노드(질문 단계), 조건 1의 시도 횟수, 대기 중인 평가 결과 등 그래프 재개에 필요한 값 전체를 담는다 |
| `graph_state_updated_at` | `timestamptz`, nullable | `graph_state`가 마지막으로 갱신된 시각. 오래 멈춘 세션을 찾아내는 운영 조회에 사용 |

- 메시지가 들어올 때마다(노드 전이가 일어날 때마다) `graph_state`를 덮어쓴다.
- 다음 메시지 수신 시 `graph_state`를 읽어 해당 지점부터 그래프를 재개한다.
- [질문이 완전한가](03-scenario.md#질문이-완전한가)의 시도 횟수·판정 이력 자체는 [7.2](#72-agent_chat_answers)의
  `agent_chat_answers`에 영구 기록되므로, `graph_state`는 그 이력의 사본이 아니라 "지금 어느
  노드에서 무엇을 기다리는지"를 나타내는 재개용 스냅샷으로 한정한다.
- `agent_chat_sessions.status`가 `DONE` / `FAILED`로 종료되면 더 이상 재개할 필요가 없으므로
  `graph_state`를 `NULL`로 비우는 것도 고려한다(불필요한 상태 보관 최소화).

### 7.4 `customer_action` / `fraud_circumstance` enum 코드 상수화

#### 배경

[4.2-a 고객 행동 추출](03-scenario.md#42-a-고객-행동-추출)의 `customer_action` 19종과
[4.2-b 사기 정황 추출](03-scenario.md#42-b-사기-정황-추출)의 `fraud_circumstance` 20종이 현재
프롬프트 문자열 안에만 나열되어 있고([04-prompts.md](04-prompts.md) C.2 / C.3), 코드 상수로
정의된 곳이 없다. 이 값들은 최소 세 곳에서 각각 하드코딩되어야 한다.

- LLM 프롬프트에 나열하는 enum 목록과 설명
- 구조화 출력(structured output) 스키마의 허용값
- DB 저장 시 값 검증(CHECK 제약 등)

한 소스가 아니라 세 곳에 각각 문자열로 박아두면 값 추가·삭제·오타 수정 시 반드시 어긋난다.
`app/domain/fraud_type_codes.py`가 사기유형 코드에 대해 이미 하는 것과 같은 패턴을 그대로 적용해야 한다.

#### 설계

`app/domain/`에 `customer_action_codes.py`, `fraud_circumstance_codes.py`(또는 하나의
`agent_chat_extraction_codes.py`)를 추가해 각 19종·20종을 `fraud_type_codes.py`와 동일한
패턴으로 관리한다.

- `customer_action` 19종을 코드 상수로 정의 (예: `DETECTED_TRANSACTION_INITIATED = "detected_transaction_initiated"` 등)
- `fraud_circumstance` 20종을 코드 상수로 정의, 필요하면 `fraud_type_codes.py`의 4개 사기유형별로
  그룹을 나눈 `Mapping`도 함께 둔다(현재 프롬프트가 보이스피싱/메신저피싱/계정탈취/사기이용계좌
  섹션으로 나뉘어 있으므로)
- `frozenset`으로 전체 허용값 집합을 만들어 다음 세 곳이 모두 이 한 소스를 참조하게 한다.
  1. 프롬프트 템플릿의 enum 목록/설명 렌더링(사람이 읽는 참고용일 뿐, 이것만으로는 강제력이 없다)
  2. [4.2-a](03-scenario.md#42-a-고객-행동-추출)/[4.2-b](03-scenario.md#42-b-사기-정황-추출) 구조화 출력 스키마(Pydantic 등)의 허용값
  3. `agent_chat_answers`/추출 결과 저장 시 DB 값 검증

각 추출 프롬프트의 "허용된 enum 이외의 값은 생성하지 않습니다" 같은 규칙은 LLM에 대한
요청일 뿐 강제가 아니다. 모델이 이 규칙을 어기고 스키마 밖의 문자열을 만들어낼 가능성은
항상 있으므로, 2번과 3번은 **파이썬 코드로 강제**해야 한다.

- **2번(구조화 출력 스키마)**: `customer_action`/`fraud_circumstance`의 `type` 필드를
  Python `Enum`(또는 `Literal[*FINAL_CUSTOMER_ACTION_CODES]`)으로 선언해 구조화 출력
  파서 단계에서부터 화이트리스트 밖의 값이 들어오면 파싱이 실패하도록 강제한다.
- **3번(DB 저장)**: 파서를 통과한 뒤에도 저장 직전에 `type in FINAL_CUSTOMER_ACTION_CODES`
  / `type in FINAL_FRAUD_CIRCUMSTANCE_CODES` 여부를 애플리케이션 코드에서 다시 한 번
  명시적으로 검증한다. 이 값이 거짓이면 해당 항목은 저장하지 않고 로그로 남긴다(전체
  추출 결과를 폐기하지 않고 항목 단위로 걸러낸다). DB CHECK 제약은 이 애플리케이션
  검증을 보완하는 이중 방어선으로만 두고, 검증의 1차 책임은 항상 파이썬 코드에 둔다.

### 7.5 상담사 반환 경로 (SSE)

#### 배경

[1. 챗봇의 역할](01-overview.md#1-챗봇의-역할)은 "모니터링 담당자의 고객 안내 업무를 대신한다"는 것인데,
정작 챗봇이 처리할 수 없어 사람에게 넘겨야 하는 순간 — [4.2 챗봇 희망 여부 질문](03-scenario.md#챗봇-희망-여부-질문)의
"즉시 상담사 연결" 버튼으로 `status`가 `HANDOFF_REQUESTED`로 바뀌는 순간 — 이걸 담당자가 어떻게
알아채는지가 스펙에 없었다. 또한 `agent_cases`와 `agent_chat_sessions`는 둘 다 `transaction_id`에
`UNIQUE` 제약만 걸려 있을 뿐([app/data/model/agent.py:53-60](../../app/data/model/agent.py#L53-L60)),
서로를 가리키는 FK가 없어 간접 연결에 그친다. 담당자 화면이 "이 상담 요청이 어느 조사 사건
(`agent_cases.case_id`)에 대응하는지"를 얻으려면 매번 `transaction_id`로 조인해야 한다.

#### 설계

`HANDOFF_REQUESTED` 전이를 **SSE(Server-Sent Events)**로 프론트에 푸시한다. 프론트는 이
스트림을 구독해 실시간으로 확인한다.

- 엔드포인트 예: `GET /agent/chat-sessions/events` — `text/event-stream`을 담당자 대시보드가
  구독
- 이벤트 페이로드: `agent_chat_sessions.status`가 `HANDOFF_REQUESTED`로 전이되는 시점에
  최소한 다음 값을 포함해 발행한다.
  - `chat_session_id`
  - `transaction_id`
  - `case_id` — `agent_cases.transaction_id = agent_chat_sessions.transaction_id`로 조회해
    채워 넣는다. 프론트가 이벤트 하나로 바로 해당 `AgentCase` 상세 화면으로 이동할 수 있도록,
    간접 조인을 서버가 대신 해서 이벤트에 실어 보낸다
  - `top_fraud_types`, `requested_at`
- 서버 구현은 상태 전이가 일어나는 지점(챗봇 파이프라인에서 `status`를 `HANDOFF_REQUESTED`로
  갱신하는 코드)에서 in-process pub/sub으로 SSE 커넥션에 즉시 브로드캐스트하는 방식으로 시작한다.
  다만 서버가 여러 인스턴스로 배포되면 in-memory 브로드캐스트만으로는 다른 인스턴스에 붙은
  담당자 커넥션에 이벤트가 전달되지 않으므로, 이 경우 Postgres `LISTEN`/`NOTIFY` 또는
  아웃박스 테이블 폴링 방식으로 교체가 필요하다는 점을 남겨둔다.
- 연결 직후(새로고침·재접속 시)에는 스트림에 아직 전달되지 않은 이벤트를 놓치므로, 최초 구독
  시점에 현재 `status = HANDOFF_REQUESTED`인 세션 목록을 스냅샷으로 한 번 내려주고 이후부터
  실시간 이벤트를 잇는다.
- 담당자가 상담을 넘겨받아 처리를 마치면 `status`를 `DONE`(또는 `FAILED`)으로 전이시키는
  별도 API가 필요하다. 현재 상태값에는 "담당자가 처리 중"을 나타내는 상태가 없어
  `HANDOFF_REQUESTED`가 그대로 유지된 채 종료되므로, 필요하면 상태값 확장 여부를 별도로 정한다.

## 5. 제약조건

모든 JSON 응답은 프롬프트에 의존하지 않고 템플릿 등으로 강제한다.
구체적인 강제 방법은 [7.4](#74-customer_action--fraud_circumstance-enum-코드-상수화)를 따른다.
