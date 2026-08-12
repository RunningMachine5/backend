# 고객 대응 챗봇 설계 — 작동 시나리오

문서 색인은 [`01-overview.md`](01-overview.md)를 참고한다.

> 이 문서는 **흐름과 분기 조건**만 다룬다. 실제 질문 문구·안내 메시지·LLM 프롬프트 전문은
> [`04-prompts.md`](04-prompts.md)에 있으며, 여기서는 어느 템플릿을 쓰는지 색인만 명시한다.
> 테이블·컬럼 정의는 [`02-db-schema.md`](02-db-schema.md)에 있다.

## 4. 작동 시나리오

### 4.1 채팅 생성·접속 단계

#### 채팅 세션 생성

FDS 파이프라인에서 이상거래로 판단된 거래가 있으면 채팅 세션 생성 함수를 호출한다.
거래 하나당 채팅 세션은 하나이며, 하나의 거래는 한 번만 판단된다.

- `CreateChatRequest`: 거래 id와 상위 2개 사기유형
  (이 사기유형은 다음 단계에서 챗봇이 어떤 질문을 할지 결정하는 데 사용된다)
- `CreateChatResponse`: 생성된 채팅 세션 id

#### 상위 2개 사기유형 선정

`CreateChatRequest`의 상위 2개 사기유형은 해당 거래의 `FraudTypeScoreResult.type_scores`
(`dict[str, float]`, [app/dto/agent.py:47](../../app/dto/agent.py#L47))에서 점수가 높은 순으로
2개를 뽑아 정한다. 다음 경우들을 순서대로 처리한다.

1. **`rule_filter_status != APPLIED`인 경우** (`SKIPPED_NOT_FRAUD` 또는 `FAILED`,
   [app/domain/agent_status.py:14](../../app/domain/agent_status.py#L14)): 룰 엔진이 유형별
   점수를 산출하지 않았으므로 `type_scores`가 비어 있거나 신뢰할 수 없다. 이 경우
   `top_fraud_types`는 **폴백 값**을 사용한다(아래 폴백 규칙 참조).
2. **`type_scores`가 비어 있거나 전부 0점인 경우**: 점수로 순위를 매길 근거가 없으므로
   마찬가지로 폴백 값을 사용한다.
3. **동점인 경우**: 2위 자리에 동점이 여럿이면 `app/domain/fraud_type_codes.py`에 정의된
   `VOICE_PHISHING → MESSENGER_PHISHING → ACCOUNT_TAKEOVER → FRAUD_USED_ACCOUNT` 코드 정의
   순서를 타이브레이커로 사용해 앞선 코드를 채택한다(정렬 키를 `(-score, 코드 정의 순서)`로 둔다).

**폴백 규칙**: 상위 2개를 점수로 정할 수 없는 모든 경우(1, 2번), `top_fraud_types`를
`NULL`로 두지 않고 [유형 판별 질문 표](04-prompts.md#b5-유형-판별-질문)의 **첫 번째 행**인
`[VOICE_PHISHING, MESSENGER_PHISHING]`(보이스피싱 vs 메신저피싱)을 기본값으로 채택한다.
이렇게 하면 `top_fraud_types`가 `NULL`이라 유형 판별 질문 자체를 만들지 못하는 상황을 없앤다.

조건: `transactions`와 연관된 `customers` 테이블의 `birthyear` 컬럼에서 60세 이상인가?

- 참: `agent_chat_sessions.is_older = true`
- 거짓: `agent_chat_sessions.is_older = false`

`agent_chat_sessions.status = URL_SENT` (기본값, 챗봇URL전송)

#### 이상거래 고객에게 이메일 전송

실제 메일 API는 연동하지 않아도 된다(다른 동료가 구현).
유저 이메일 정보는 `transactions`의 연관 테이블에 저장되어 있다.

생성된 채팅 세션 id 접근 URL을 전송한다.

이메일 발송 시점에도 `agent_chat_sessions.status = URL_SENT`를 유지한다.

이메일로 코드를 접속한 뒤 본인인증을 진행한다(출생연도 4자리 인증을 넣는 간이 방식).

- 성공: 챗봇 접속
- 실패: 본인인증 재시도

### 4.2 정보 수집 단계 (챗봇 로직)

조건: 챗봇 URL 접속 시 `agent_chat_sessions.is_older` 확인

- 참: 고령자 전용 UI로 이동 (추후 구현)
- 거짓: 기본 챗봇 UI로 이동

#### 최초 알림 메시지

거래시각, 거래금액, 입금/출금은 DB에서 조회해 템플릿에 채운다.
(실제로 보류를 구현하지는 않는다.)

→ 문구: [04-prompts.md A.1 최초 알림 메시지](04-prompts.md#a1-최초-알림-메시지)

#### 챗봇 희망 여부 질문

최초 알림 메시지 이후 유저에게 퀵리플라이 3버튼(챗봇 상담 받을게요 / 괜찮아요 / 즉시 상담사 연결)을
노출하며, 이 단계에서는 **텍스트 입력창을 비활성화**한다. 유저는 3버튼 중 하나를 선택해야만
다음 단계로 진행할 수 있다.

| 버튼 | 동작 | 상태 전이 |
| --- | --- | --- |
| 챗봇 상담 받을게요 | 공통질문 1 응답 | `URL_SENT` → `IN_PROGRESS` |
| 괜찮아요 | 거래 정지 해제 링크와 구체적 절차를 알려주고 상담 종료 | `URL_SENT` → `DONE` |
| 즉시 상담사 연결 | 대기 안내 메시지 출력 | `URL_SENT` → `HANDOFF_REQUESTED` |

→ 버튼 문구와 출력 메시지: [04-prompts.md A.2 퀵리플라이 버튼 문구](04-prompts.md#a2-퀵리플라이-버튼-문구)

#### 공통질문 1~4

공통질문 1~4를 순서대로 진행하며, 각 질문마다 [질문이 완전한가](#질문이-완전한가) 검사를
거친 뒤 고객응답을 저장한다.

| 순서 | `question_key` | 질문 문구 | 흐름 |
| --- | --- | --- | --- |
| 공통질문 1 | `COMMON_1` | [04-prompts.md B.1](04-prompts.md#b1-공통질문-1) | → 질문이 완전한지 검사 → 고객응답 1 저장 |
| 공통질문 2 | `COMMON_2` | [04-prompts.md B.2](04-prompts.md#b2-공통질문-2) | → 질문이 완전한지 검사 → 고객응답 2 저장 |
| 공통질문 3 | `COMMON_3` | [04-prompts.md B.3](04-prompts.md#b3-공통질문-3) | → 질문이 완전한지 검사 → 고객응답 3 저장 |
| 공통질문 4 | `COMMON_4` | [04-prompts.md B.4](04-prompts.md#b4-공통질문-4) | → 질문이 완전한지 검사 → 고객응답 4 저장 |

#### 유형 판별 질문

`agent_chat_sessions.top_fraud_types`에 저장된 상위 2개 사기유형 중 확실한 것을
판단하기 위한 질문이다. 상위 2개 사기유형 조합에 따라 질문이 달라진다.
6개 조합별 질문 문구는 [04-prompts.md B.5 유형 판별 질문](04-prompts.md#b5-유형-판별-질문)에 있다.

`question_key`는 `TYPE_DISCRIMINATION`이며, 이 질문에는 [질문이 완전한가](#질문이-완전한가)
평가 LLM을 적용하지 않는다.

→ 고객응답 5 저장

고객응답 1+2+3+4+5 ⇒ **고객 응답 리스트**로 병합
(병합 방법은 [02-db-schema.md 7.2 `agent_chat_answers`](02-db-schema.md#72-agent_chat_answers) 참고)

---

#### 질문이 완전한가

고객응답 1~5에 대한 질문을 평가하고 다음 질문으로 넘어갈지 결정하는 로직(에이전트).
고객 응답에 따라 **다음질문** 또는 **재질문**으로 분기된다.

**조건 1: 현재 질문에 대한 재시도 횟수**

질문당 추가 재질문 최대 1회를 넘어가면 그냥 다음질문으로 pass.

**조건 2: 고객응답 평가 LLM**

| 판정 | 동작 |
| --- | --- |
| `SUFFICIENT` | 다음 질문 |
| `TOO_VAGUE` | 재질문 안내 문구 출력과 함께 재질문 |
| `NON_ANSWER` | 재질문 안내 문구 출력과 함께 재질문 |
| `REFUSAL` | 안내 문구 출력과 함께 다음질문 |

→ 판정별 안내 문구: [04-prompts.md A.3 재질문·판정별 안내 문구](04-prompts.md#a3-재질문판정별-안내-문구)
→ 평가 LLM 프롬프트: [04-prompts.md C.1 고객응답 평가 LLM 프롬프트](04-prompts.md#c1-고객응답-평가-llm-프롬프트)

**평가 LLM 실패 시 재시도·타임아웃**

`ml_serving`과 무관한 별도 정책이다. 호출당 `CHAT_QUALITY_CHECK_TIMEOUT_SECONDS`(예: 8초)
타임아웃을 두고, 타임아웃·5xx·커넥션 오류에 한해 `CHAT_QUALITY_CHECK_MAX_ATTEMPTS`(예: 2,
최초 1회 + 재시도 1회)까지 `CHAT_QUALITY_CHECK_RETRY_DELAY_SECONDS`(예: 0.5초) 간격으로
재시도한다. 4xx나 응답 스키마 위반처럼 재시도해도 같은 결과가 나올 오류는 재시도하지 않는다.

재시도를 모두 소진했는데도 평가 LLM 호출이 끝내 실패하면, 이는 고객 응답의 **내용**에 대한
판단이 아니라 **시스템 장애**이므로 `TOO_VAGUE`/`NON_ANSWER`로 몰아 사용자에게 재입력을
강요하지 않는다. `REFUSAL`과 동일하게 다음 질문으로 자동 진행시키되, 평가 자체가 이뤄지지
않았다는 사실을 남기기 위해 [7.2 `agent_chat_answers`](02-db-schema.md#72-agent_chat_answers)의
`quality_verdict`는 `NULL`로 저장한다(조건 1 재시도 초과·`TYPE_DISCRIMINATION`으로 인한
`NULL`과 동일한 값이지만, 원인은 로그로 구분한다).

---

#### 4.2-a 고객 행동 추출

추출 대상: 고객 응답 리스트

- 추출 결과 형식: [04-prompts.md D.1 `customer_actions`](04-prompts.md#d1-customer_actions)
- 추출 프롬프트: [04-prompts.md C.2 고객 행동 추출 LLM 프롬프트](04-prompts.md#c2-고객-행동-추출-llm-프롬프트)
  (`customer_action` 19종 enum 정의 포함)

enum 값은 프롬프트가 아니라 파이썬 코드로 강제한다
([02-db-schema.md 7.4](02-db-schema.md#74-customer_action--fraud_circumstance-enum-코드-상수화)).

#### 4.2-b 사기 정황 추출

추출 대상: 고객 응답 리스트

- 추출 결과 형식: [04-prompts.md D.2 `fraud_circumstances`](04-prompts.md#d2-fraud_circumstances)
- 추출 프롬프트: [04-prompts.md C.3 사기 정황 추출 LLM 프롬프트](04-prompts.md#c3-사기-정황-추출-llm-프롬프트)
  (`fraud_circumstance` 20종 enum 정의 포함, 4개 사기유형별로 그룹화)

#### 4.2-c 추출 LLM 실패 시 재시도·타임아웃

`ml_serving`과 무관한 별도 정책이다. [4.2-a](#42-a-고객-행동-추출) `customer_action` 추출과
[4.2-b](#42-b-사기-정황-추출) `fraud_circumstance` 추출은 서로 독립된 LLM 호출이므로 재시도
정책도 각각 독립 적용한다. 호출당 `CHAT_EXTRACTION_TIMEOUT_SECONDS`(예: 15초) 타임아웃을
두고, 타임아웃·5xx·커넥션 오류에 한해 `CHAT_EXTRACTION_MAX_ATTEMPTS`(예: 2, 최초 1회 +
재시도 1회)까지 `CHAT_EXTRACTION_RETRY_DELAY_SECONDS`(예: 0.5초) 간격으로 재시도한다.

두 추출 중 하나가 재시도를 모두 소진하고도 끝내 실패하면, 다른 하나의 성공 여부와 무관하게
**해당 추출 결과만 빈 리스트로 간주**하고 나머지 파이프라인은 계속 진행한다(둘 다 실패해도
각각 독립적으로 아래 규칙이 적용된다).

- **`customer_actions` 추출 실패 → 빈 리스트로 간주**: [4.3-a](#43-a-customer_actions-rag-검색-과정)의
  Retrieve 단계에 근거 청크가 없더라도 챗봇은 반드시 고객에게 응답해야 하므로, 사고 대응
  공통 안내(계좌 정지 해제 절차 등 일반 가이드)로 폴백해 응답을 생성한다. 챗봇이 응답 없이
  멈추는 것을 최우선으로 피한다.
- **`fraud_circumstances` 추출 실패 → 빈 리스트로 간주**: 별도 예외 처리를 두지 않는다.
  [4.3-b](#43-b-fraud_circumstances-사기유형-추가-판정-과정) 판정 로직에 빈 리스트를 그대로
  흘려보내면 상위 2개 유형 모두 정황 개수 0건으로 집계되어, 이미 정의된
  [동점 처리 규칙](#43-b-fraud_circumstances-사기유형-추가-판정-과정)에 따라
  `primary_fraud_type = "DRAW"`, `primary_fraud_type_score = 0`으로 자연스럽게 귀결된다.
  이 경우 담당자는 `DRAW`+`score = 0`을 "정황 미확인 또는 추출 실패"로 함께 해석해야 한다.

---

### 4.3 정보 응답 단계 (RAG)

#### 4.3-a customer_actions RAG 검색 과정

입력 형식: [04-prompts.md D.1 `customer_actions`](04-prompts.md#d1-customer_actions)

**외부 조회**: `customer_actions`의 답변 원문(evidence)에 URL, 전화번호/계좌, 이메일이 존재하는가?

| 대상 | 조회처 |
| --- | --- |
| URL | Google Safe Browsing API |
| 전화번호/계좌 | 더치트 API |
| 이메일 | 경찰청 이메일 조회 사이트 링크 출력 |

각 API 응답을 토대로 URL/전화번호/계좌/이메일 불량 여부를 Augment 단계에 추가한다.
이 외부 조회에 남은 문제는 [05-open-issues.md](05-open-issues.md)를 참고한다.

**검색 질의 구성 (enum → 한국어 매핑)**

`customer_action`은 `phishing_link_opened`처럼 영문 스네이크케이스 코드이고, `cs_guide_documents`의
가이드 문서는 한국어다. 코드 문자열을 그대로 임베딩해 검색하면 언어가 달라 유사도가 잡히지 않는다.
`evidence` 원문(고객 답변)만 검색 질의로 쓰는 방법도 있지만, 답변이 "링크 눌렀어요"처럼 짧으면
임베딩 신호가 약해 관련 청크를 놓치기 쉽다.

`FRAUD_TYPE_DISPLAY_NAMES`([app/domain/fraud_type_codes.py:15](../../app/domain/fraud_type_codes.py#L15))와
같은 패턴으로, [02-db-schema.md 7.4](02-db-schema.md#74-customer_action--fraud_circumstance-enum-코드-상수화)에서
정의하는 `customer_action_codes.py`에 검색 질의용 한국어 매핑을 함께 둔다.

→ 매핑·질의 템플릿: [04-prompts.md E. RAG 검색 질의 템플릿](04-prompts.md#e-rag-검색-질의-템플릿)

매핑 문구가 검색이 걸리는 기본 신호를 담당하고, `evidence`는 고객의 구체적 상황(어떤
링크였는지, 언제였는지 등)을 덧붙여 검색 결과를 보정하는 역할을 한다.

**RAG 단계: 액션별 개별 Retrieve + Generate (병합하지 않음)**

`customer_actions`는 액션 개수가 매번 다르다(1개일 수도, 5개일 수도 있다). 여러 액션의
검색 질의를 하나로 합쳐 한 번에 Retrieve하고 그 결과를 하나의 프롬프트로 Generate하는
방식은 채택하지 않는다. 이 방식은 두 가지 문제가 있다.

- 여러 질의를 하나의 벡터 검색으로 합치려면 결과를 다시 병합(RRF 등)하고 중복을 제거하는
  로직이 필요하고, `top_k`도 액션 개수에 맞춰 매번 조정해야 한다.
- Generate 프롬프트 안에 "액션 1개일 때/3개일 때/5개일 때"를 모두 다루는 가변 길이 목록을
  넣어야 하므로, 고정된 프롬프트 템플릿 하나로 대응할 수 없다.

대신 **액션 하나당 Retrieve→Generate를 독립적으로 한 번씩 수행**해 액션별 대응 가이드
조각을 만들고, 마지막에 조각들을 이어붙여 하나의 응답으로 반환한다. 액션이 몇 개든 반복
횟수만 늘어날 뿐 프롬프트 자체는 항상 "액션 1개"를 다루는 고정된 템플릿이므로, 개수에
따라 프롬프트 구조를 바꿀 필요가 없다.

```
for action in customer_actions:
    query = f"{CUSTOMER_ACTION_SEARCH_QUERIES[action.type]} {action.evidence}"
    chunks = retrieve(query, top_k=3)        # 액션 1개당 고정 top_k=3
    guide_fragment = generate(action, chunks)  # 액션 1개 전용 고정 프롬프트
    fragments.append(guide_fragment)

response = assemble(fragments)  # 액션별 소제목을 붙여 순서대로 이어붙임
```

- **Retrieve**: 위에서 구성한 질의로 유사한 내용의 청크를 Vector DB에서 가져온다
- **Augment**: 기본 프롬프트에 유사한 청크와 외부 조회 결과를 추가한다
- **Generate**: 챗봇 응답 생성을 위해 LLM을 호출한 응답값을 고객에게 출력한다

세부 규칙:

- `top_k`는 액션 개수와 무관하게 액션 1개당 고정값(예: 3)으로 둔다. 병합 검색이 아니라
  액션별 독립 검색이므로 애초에 액션 수에 맞춰 조정할 필요가 없다.
- `assemble` 단계는 프롬프트가 아니라 애플리케이션 코드에서 문자열을 이어붙이는 정형화된
  로직으로 둔다(예: 액션별 소제목 + `guide_fragment` 순서대로 나열). 이 단계까지 LLM에
  맡기면 다시 "가변 개수의 조각을 하나의 응답으로 요약"하는 가변 길이 프롬프트 문제로
  되돌아가므로 피한다.

**검색 결과 0건 시 상담사 연결로 분기**

액션별 Retrieve 중 **하나라도** 가져온 청크가 0건이면(임계 유사도 이상 매칭이 하나도
없으면), 그 액션만 건너뛰지 않고 세션 전체를 상담사 연결로 분기한다. 일부 액션만 근거
없이 답변하고 나머지는 정상 안내하면 고객이 어느 부분이 부정확한지 구분할 수 없어
[1. 챗봇의 역할](01-overview.md#1-챗봇의-역할)의 "정확한 고객 대응 가이드 제공"에 반하는
위험이 남기 때문이다. 근거 청크 없이 LLM이 대응 가이드를 생성하는 것도 마찬가지 이유로
피한다. 대신 [챗봇 희망 여부 질문](#챗봇-희망-여부-질문)의 "즉시 상담사 연결"과
동일하게 처리한다.

- 상담사 연결 안내 문구 출력
  ([04-prompts.md A.4](04-prompts.md#a4-상담사-연결-안내-문구))
- `agent_chat_sessions.status = HANDOFF_REQUESTED`로 전이
- [02-db-schema.md 7.5 상담사 반환 경로](02-db-schema.md#75-상담사-반환-경로-sse)의 SSE 이벤트를
  그대로 발행해 담당자에게 알린다

**추가 질문**

최초 대응가이드 응답 이후 유저는 추가 질문이 가능하다.
추가 질문에 대해 대응가이드가 저장된 벡터 저장소를 추가 문맥으로 하여 응답을 진행한다.
(대화 히스토리 미사용 문제는 [05-open-issues.md](05-open-issues.md) 참고)

#### 4.3-b fraud_circumstances 사기유형 추가 판정 과정

입력 형식: [04-prompts.md D.2 `fraud_circumstances`](04-prompts.md#d2-fraud_circumstances)

`fraud_circumstance` enum은 4가지 사기유형별로 나뉘어 정의되어 있지만, 집계는
**4가지 전부가 아니라 `agent_chat_sessions.top_fraud_types`의 상위 2개 유형만** 검사한다.
상위 2개에 속하지 않는 유형의 정황이 추출되더라도 집계에서 제외한다.
따라서 `top_fraud_types`에 없던 유형이 `primary_fraud_type`이 되는 일은 없다.

판정 규칙:

1. 추출된 `fraud_circumstances`의 `type`을 상위 2개 유형별로 각각 개수를 센다.
2. 개수가 더 많은 유형이 `fraud_type_score_after_chat.primary_fraud_type`이 된다.
3. `primary_fraud_type_score`는 두 유형의 정황 개수 차이의 절댓값이다.
   이 점수가 클수록 `primary_fraud_type`의 정확도가 높은 것으로 이해한다.

동점 처리:

- 두 유형의 개수가 같으면(둘 다 0개인 경우 포함) `primary_fraud_type = "DRAW"`로 저장한다.
- 이때 개수 차이가 0이므로 `primary_fraud_type_score = 0`이다.

예시: `top_fraud_types = [VOICE_PHISHING, MESSENGER_PHISHING]`

| 보이스피싱 정황 수 | 메신저피싱 정황 수 | `primary_fraud_type` | `primary_fraud_type_score` |
| --- | --- | --- | --- |
| 3 | 1 | `VOICE_PHISHING` | 2 |
| 0 | 2 | `MESSENGER_PHISHING` | 2 |
| 2 | 2 | `DRAW` | 0 |
| 0 | 0 | `DRAW` | 0 |
