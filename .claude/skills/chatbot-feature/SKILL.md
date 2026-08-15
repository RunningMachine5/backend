---
name: chatbot-feature
description: Implement or modify the customer-response chatbot — chat sessions, the question flow, customer_action/fraud_circumstance extraction, RAG guide answers, fraud-type scoring, and handoff to a human agent. Use when work touches app/services/{chatbot,rag}/, app/pipelines/customer_chatbot_pipeline.py, app/api/chat.py, the chat_* tables, or docs/customer-chatbot/.
---

# 고객 대응 챗봇 기능 구현

설계는 `docs/customer-chatbot/` 5개 문서가 유일한 출처다. 이 스킬은 문서에 **없는 것** —
어디를 봐야 하는지, 코드를 어디에 두는지, 작업 후 무엇을 갱신하는지 — 만 담는다.

## 1. 어디를 보는가

문서 전체를 읽지 말고 아래 표에서 필요한 것만 찾아 읽는다.

| 하려는 작업 | 볼 곳 |
| --- | --- |
| **무엇부터 볼지 모를 때** | [docs/customer-chatbot/README.md](../../../docs/customer-chatbot/README.md) — PRD 본문. 흐름과 분기 조건만 있다 |
| 세션 생성, 이메일 발송, 본인인증 | PRD 2.1 ~ 2.2 |
| 최초 알림 메시지, 버튼 3종, 상태 전이 | PRD 2.3 |
| 질문 진행(`question_step`), 재질문 횟수, 평가 LLM 판정 | PRD 2.4 |
| 고객 행동 추출, RAG 검색, 검색 0건 처리 | PRD 2.5 |
| 사기 정황 추출, 채점 시점, 중복 방지 | PRD 2.6 |
| 상담사 연결(SSE) | PRD 2.7 |
| **아직 안 정해진 것 확인** (중복 제기 금지) | PRD 3. 미해결 문제 |
| 테이블·컬럼·제약조건·마이그레이션 순서 | [schema.md](../../../docs/customer-chatbot/schema.md) |
| LLM에 보내는 프롬프트 전문, enum 19종/20종 정의 | [prompts.md](../../../docs/customer-chatbot/prompts.md) |
| 고객에게 출력하는 안내 문구 | [messages.md](../../../docs/customer-chatbot/messages.md) |
| 정황 → 사기유형 점수 환산표 | [scoring.md](../../../docs/customer-chatbot/scoring.md) |

문서끼리 상호 링크가 걸려 있으므로, 한 절을 읽다 다른 문서 참조가 나오면 따라간다.
PRD 맨 아래 **4. 부속 문서 색인**에 A.1~A.3 / B.1~B.6 / 스키마 3.x 전체가 역방향 링크와 함께 있다.

## 2. 코드 배치

레이어는 레포 전체 규칙(`main.py` → `api` → `pipelines` → `services` → `repositories` →
`data/model`)을 그대로 따른다.

| 종류 | 위치 |
| --- | --- |
| 라우터 (`/chat`) | `app/api/chat.py` |
| 오케스트레이션 | `app/pipelines/customer_chatbot_pipeline.py` |
| LangGraph 노드, 질문 진행, 평가/추출 호출 | `app/services/chatbot/` |
| 임베딩, 검색, 질의 구성 | `app/services/rag/` |
| 세션·메시지·답변·추출 영속화 | `app/repositories/` (신규: `chat_session.py` 등) |
| SQLModel 테이블 | `app/data/model/chatbot.py` |
| DTO | `app/dto/chatbot.py` |
| enum 코드 상수 | `app/domain/` (`fraud_type_codes.py` 패턴) |
| 설정값 (타임아웃·재시도·URL) | `app/core/config.py` (env var, settings 클래스 없음) |

## 3. 지켜야 할 것

- **프롬프트·문구를 코드에 새로 쓰지 않는다.** `prompts.md` /
  `messages.md`의 것을 그대로 옮기고, 없는 문구가 필요하면 문서에 먼저 추가한다.
- **enum은 파이썬에서 강제한다.** 스키마 3.8대로 `app/domain/`에 한 소스를 두고, 구조화 출력
  스키마와 저장 직전 검증이 모두 그 상수를 참조한다. 프롬프트에 나열된 것만으로는 강제가 아니다.
- **채점표와 코드 상수는 한 소스다.** `scoring.md`의 표와
  `FRAUD_CIRCUMSTANCE_SCORES`를 함께 고친다.
- **커밋/트랜잭션은 파이프라인이 소유한다.** `get_session`은 commit하지 않는다.
- **테이블을 추가·변경하면** `app/data/model/__init__.py`에서 도달 가능해야 하고
  (아니면 autogenerate가 `DROP TABLE`을 낸다), `migrations/versions/`에 새 리비전을 쌓는다.
  이미 병합된 마이그레이션은 수정하지 않는다. 부분 유니크 인덱스는 autogenerate가 잡지 못하므로
  손으로 넣는다.
- 주석·docstring은 한국어로 쓴다.

## 4. 현재 구현 상태

**챗봇 Fake는 전부 걷어냈다.** `customer_chatbot_pipeline.py`, `fake_embedder.py`,
`fake_guide_retriever.py`, `fake_transaction_repository.py`, `customer_chatbot.py`
(`build_chatbot_chain`), 구 `ChatbotRequestDTO`/`CustomerGuideDTO`/`ChatbotResponseDTO`가
모두 삭제됐다. `app/services/rag/`의 `fake_llm.py`·`fake_vector_db.py`는 Agent
(`monitoring_agent_pipeline.py`)가 쓰므로 남아 있다 — 챗봇 쪽에서 쓰지 않는다.

실제 구현 진행 상황은 [implementation-plan.md](../../../docs/customer-chatbot/implementation-plan.md)의
체크박스가 기준이다. 대략:

- **완료**: 도메인 상수·채점표(`app/domain/`), 테이블·마이그레이션(`app/data/model/chatbot.py`),
  DTO 재정의(`app/dto/chatbot.py`), 구조화 리트리버(`retriever_source` — 0건은 빈 리스트),
  챗봇 리포지토리, 평가·추출 LLM 서비스(`answer_evaluator.py`, `extractors.py`),
  RAG 응답 조립(`guide_responder.py`), 채점 집계(`chat_scoring.py`)
- **남은 것**: LangGraph 파이프라인(6단계), API + 세션 생성·이메일 발송 + SSE(7단계),
  FDS 결합(8단계). `app/api/chat.py`에는 빈 라우터만 있다.

임베딩 적재와 pgvector 코사인 검색(`app/services/rag/`)은 처음부터 실제 구현이다.

## 5. 구현 순서

의존 방향대로 아래에서 위로 쌓는다. 한 번에 전부 하지 말고 단계마다 테스트를 남긴다.

1. **도메인 상수** — `customer_action` 19종 / `fraud_circumstance` 20종 + 검색 질의 한국어 매핑
   + 채점표 (스키마 3.8)
2. **테이블** — `chat_answers` / 고객 행동·사기 정황 추출 테이블 신설,
   `chat_sessions`·`fraud_type_score_after_chat` 변경 (스키마 3.4~3.7) + 마이그레이션
3. **DTO** — 추출 결과 구조화 출력 스키마. `type` 필드는 1번 상수를 참조하는 `Enum` / `Literal`
4. **리트리버 구조화** — 0건을 빈 리스트로 반환 (PRD 2.5의 선결 조건)
5. **서비스** — 질문 진행, 평가 LLM, 추출 LLM, RAG. 재시도·타임아웃은 `app/core/config.py` env var
6. **파이프라인** — LangGraph 그래프 조립(체크포인터는 `InMemorySaver`), 상태 전이
7. **API** — 세션 생성, 메시지 송수신, 본인인증, SSE 반환 경로 (PRD 2.7)

## 6. 테스트

unittest를 쓴다(pytest 아님). `tests/test_chatbot_*.py`로 추가한다.

```bash
uv run python -m unittest discover -s tests -v
```

LLM·외부 API를 실제로 부르지 않는다. CI가 `OPENAI_API_KEY=test-only-key`로 돌기 때문에
실제 호출이 있으면 깨진다. 판정 분기(`SUFFICIENT`/`TOO_VAGUE`/`NON_ANSWER`/`REFUSAL`/`WANT_END`),
재시도 소진 시 폴백, 검색 0건 분기, 채점 집계와 동점 처리 같은 **분기 로직**을 대상으로 삼는다.

## 7. 작업 후 문서 갱신 (필수)

구현하면서 설계와 다르게 결정한 것이 생기면 코드만 고치고 끝내지 않는다. 문서가 곧 다음
작업자의 입력이므로, 어긋난 문서는 잘못된 구현을 만든다.

| 무엇이 바뀌었나 | 갱신할 문서 |
| --- | --- |
| 흐름·분기·폴백·재시도 값 | `README.md` 2장 |
| 미뤄둔 문제, 새로 알게 된 제약 | `README.md` 3장 |
| 테이블·컬럼·상태값·제약조건 | `schema.md` |
| LLM 프롬프트 | `prompts.md` |
| 고객에게 보이는 문구 | `messages.md` |
| 정황별 점수 | `scoring.md` + `FRAUD_CIRCUMSTANCE_SCORES` |
| 절 구성이 바뀜 | `README.md`의 4. 부속 문서 색인 |

문서 간 상호 링크와 코드 링크(`../../app/...`)를 함께 유지한다.
새 절을 추가하면 색인에도 한 줄 넣는다.

작업 요약에는 **어느 문서의 어느 절을 갱신했는지** 명시한다. 갱신하지 않았다면
"설계 변경 없음"이라고 명시적으로 말한다.
