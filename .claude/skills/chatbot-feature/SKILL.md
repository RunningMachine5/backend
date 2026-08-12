---
name: chatbot-feature
description: Implement or modify the customer-response chatbot (chat sessions, question flow, customer_action/fraud_circumstance extraction, RAG guide answers, handoff to a human agent). Use when work touches app/services/{chatbot,rag}/, app/pipelines/customer_chatbot_pipeline.py, app/api/chat.py, the agent_chat_* tables, or docs/customer-chatbot/.
---

# 고객 대응 챗봇 기능 구현

설계는 `docs/customer-chatbot/` 5개 문서가 유일한 출처다. 이 스킬은 문서에 **없는 것** —
구현 순서, 코드 배치 규칙, 작업 후 문서 갱신 의무 — 만 담는다.

## 1. 먼저 읽기

작업 시작 전 반드시 해당 절을 읽는다. 문서 전체를 읽지 말고 색인으로 필요한 절만 찾는다.

| 무엇을 만드는가 | 읽을 문서 |
| --- | --- |
| 전체 색인 (항상 여기서 시작) | `docs/customer-chatbot/01-overview.md` |
| 테이블·컬럼·상태값·enum 상수화 | `02-db-schema.md` |
| 흐름, 분기 조건, 폴백, 재시도 정책 | `03-scenario.md` |
| 고객 안내 문구, 질문 문구, LLM 프롬프트, 출력 스키마 | `04-prompts.md` |
| 알려진 미해결 문제 (중복 제기 금지) | `05-open-issues.md` |

문서끼리 상호 링크가 걸려 있으므로, 한 절을 읽다 다른 절 참조가 나오면 따라간다.
절 번호(3~7)는 분할 전 원본 번호를 유지한 것이라 파일명과 번호가 1:1이 아니다.

## 2. 코드 배치 규칙

레이어는 레포 전체 규칙(`main.py` → `api` → `pipelines` → `services` → `repositories` →
`data/model`)을 그대로 따른다. 챗봇 영역의 배치는 다음과 같다.

| 종류 | 위치 |
| --- | --- |
| 라우터 (`/chat`) | `app/api/chat.py` |
| 오케스트레이션 | `app/pipelines/customer_chatbot_pipeline.py` |
| LangGraph 노드, 질문 진행, 평가/추출 호출 | `app/services/chatbot/` |
| 임베딩, 검색, 질의 구성 | `app/services/rag/` |
| 세션·메시지·답변 영속화 | `app/repositories/` (신규 파일: `chat_session.py` 등) |
| SQLModel 테이블 | `app/data/model/agent.py` |
| DTO | `app/dto/chatbot.py` |
| enum 코드 상수 | `app/domain/` (`fraud_type_codes.py` 패턴) |
| 설정값 (타임아웃·재시도) | `app/core/config.py` (env var, settings 클래스 없음) |

지켜야 할 것:

- **프롬프트·질문·안내 문구를 코드에 새로 쓰지 않는다.** `04-prompts.md`의 문구를 그대로
  옮기고, 문서에 없는 문구가 필요하면 문서에 먼저 추가한 뒤 옮긴다.
- **enum은 파이썬에서 강제한다.** `02-db-schema.md` 7.4대로 `app/domain/`에 한 소스를 두고,
  구조화 출력 스키마와 저장 직전 검증이 모두 그 상수를 참조한다. 프롬프트 문자열에 나열된
  것만으로는 강제가 아니다.
- **커밋/트랜잭션은 파이프라인이 소유한다.** `get_session`은 commit하지 않는다.
- **테이블을 추가·변경하면** `app/data/model/__init__.py`에서 도달 가능해야 하고
  (아니면 autogenerate가 `DROP TABLE`을 낸다), `migrations/versions/`에 마이그레이션을 만든다.
  이미 병합된 마이그레이션은 수정하지 말고 새 리비전을 쌓는다.
- 주석·docstring은 한국어로 쓴다.

## 3. 현재 상태 (Fake 스켈레톤)

챗봇 영역은 아직 대부분 Fake다. 실제 구현으로 교체할 때 무엇을 걷어내는지 알고 시작한다.

- `app/services/chatbot/`: `fake_embedder.py`, `fake_guide_retriever.py`,
  `fake_transaction_repository.py`
- `app/services/rag/`: `fake_llm.py`, `fake_vector_db.py`
- `app/pipelines/customer_chatbot_pipeline.py`: 위 Fake들을 엮은 껍데기
- `app/services/chatbot/customer_chatbot.py`: `build_chatbot_chain`은 실제 LangChain RAG 체인이지만
  `question` 문자열 하나만 받는다(대화 히스토리 미사용 — `05-open-issues.md`에 기록된 알려진 문제)
- `app/api/chat.py`: `/chat/ask` 하나뿐이며 세션 개념이 없다

`cs_guide_documents` / `cs_guide_document_chunks` 임베딩 적재와 pgvector 검색은 실제로
동작하는 부분이다(`app/services/rag/`, `app/repositories/agent_guide.py`).

## 4. 구현 순서

의존 방향대로 아래에서 위로 쌓는다. 한 번에 전부 하지 말고 단계마다 테스트를 남긴다.

1. **도메인 상수** — `customer_action` 19종 / `fraud_circumstance` 20종을 `app/domain/`에
   (`02-db-schema.md` 7.4)
2. **테이블** — `agent_chat_answers` 신설, `agent_chat_sessions`에 `graph_state` 추가
   (`02-db-schema.md` 7.2 / 7.3) + 마이그레이션
3. **DTO** — 추출 결과 구조화 출력 스키마 (`04-prompts.md` D). `type` 필드는 1번 상수를 참조하는
   `Enum` / `Literal`로 선언
4. **서비스** — 질문 진행, 평가 LLM, 추출 LLM, RAG 검색. 재시도·타임아웃 정책은
   `03-scenario.md`의 값을 `app/core/config.py` env var로
5. **파이프라인** — LangGraph 그래프 조립, 상태 전이(`URL_SENT` → … → `DONE`/`HANDOFF_REQUESTED`),
   `graph_state` 영속화
6. **API** — 세션 생성, 메시지 송수신, 본인인증. SSE 반환 경로는 `02-db-schema.md` 7.5

## 5. 테스트

unittest를 쓴다(pytest 아님). `tests/test_chatbot_*.py`로 추가하고 다음으로 확인한다.

```bash
uv run python -m unittest discover -s tests -v
```

LLM·외부 API 호출은 실제로 부르지 않는다. CI는 `OPENAI_API_KEY=test-only-key`로 돌기 때문에
호출이 있으면 깨진다. 판정 분기(`SUFFICIENT`/`TOO_VAGUE`/`NON_ANSWER`/`REFUSAL`), 재시도 소진
시 폴백, 추출 실패 시 빈 리스트 처리, 동점 시 `DRAW` 같은 **분기 로직**을 대상으로 삼는다.

## 6. 작업 후 문서 갱신 (필수)

구현하면서 설계와 다르게 결정한 것이 생기면 코드만 고치고 끝내지 않는다. 문서가 곧
다음 작업자의 입력이므로, 어긋난 문서는 잘못된 구현을 만든다.

| 무엇이 바뀌었나 | 갱신할 문서 |
| --- | --- |
| 테이블·컬럼·상태값·제약조건 | `02-db-schema.md` |
| 흐름·분기·폴백·재시도 값 | `03-scenario.md` |
| 고객에게 보이는 문구, 질문, 프롬프트 | `04-prompts.md` |
| 미뤄둔 문제, 알게 된 제약 | `05-open-issues.md` |
| 절 구성이 바뀜 | `01-overview.md`의 색인 |

문서 간 상호 링크(`03-scenario.md#...` 형태)와 코드 링크(`../../app/...`)를 함께 유지한다.
새 절을 추가하면 `01-overview.md`의 문서별 목차에도 한 줄 넣는다.

작업 요약에는 **어느 문서의 어느 절을 갱신했는지** 명시한다. 갱신하지 않았다면
"설계 변경 없음"이라고 명시적으로 말한다.
