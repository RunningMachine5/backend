# FDShield Agent 대응 가이드 코퍼스

이 디렉터리는 Agent의 대응 가이드 RAG에 사용할 검토 완료 Markdown 문서를 관리한다.
원본 PDF와 웹페이지는 공식 출처로 남기고, 검색에 사용할 핵심 내용만 요약·정규화한다.

## 디렉터리 구조

```text
agent_guides/
├── official/       # 공식 기관의 공개 자료를 요약한 문서
└── internal_demo/  # FDShield 시연용 모니터링 절차와 체크리스트
```

## 문서 구분 원칙

- `OFFICIAL_GUIDE`는 원문에서 확인할 수 있는 내용만 요약한다.
- `INTERNAL_DEMO_GUIDE`는 프로젝트 시연을 위해 작성한 가상의 내부 절차이다.
- 내부 문서를 금융감독원·금융보안원 등 외부 기관의 공식 지침으로 표현하지 않는다.
- 공식 원문을 장문으로 복사하지 않고 RAG에 필요한 내용을 재서술한다.

## Front matter 계약

모든 검색 대상 문서는 다음 필드를 갖는다.

```yaml
---
document_id: FSS-MESSENGER-PHISHING-2022-13
title: 가족·지인 사칭 메신저피싱 소비자경보 요약
source_type: OFFICIAL_GUIDE
source_name: 금융감독원
source_url: https://www.fss.or.kr/...
fraud_types:
  - MESSENGER_PHISHING
audiences:
  - CUSTOMER
topics:
  - MESSENGER_IDENTITY_CHECK
risk_grades:
  - HIGH
  - VERY_HIGH
action_codes:
  - GUIDE_SEPARATE_CONTACT_CHECK
  - GUIDE_MESSENGER_PHISHING_RESPONSE
version: "1.0"
published_at: 2022-10-13
accessed_at: 2026-08-09
---
```

## 허용 코드

### 사기 유형

```text
VOICE_PHISHING
MESSENGER_PHISHING
ACCOUNT_TAKEOVER
FRAUD_USED_ACCOUNT
```

### 대상 사용자

```text
MONITORING
CUSTOMER
COMMON
```

### 대응 주제

```text
CUSTOMER_CONFIRMATION
SECURITY_CHECK
RECIPIENT_ACCOUNT_REVIEW
ADDITIONAL_TRANSACTION_REVIEW
ACCOUNT_FLOW_REVIEW
DAMAGE_REPORT
MESSENGER_IDENTITY_CHECK
EMERGENCY_RESPONSE
MANUAL_REVIEW
```

### 위험등급

```text
LOW
MEDIUM
HIGH
VERY_HIGH
```

### 정책 조치 코드와 문서 버전

- `action_codes`는 `response_policies.yaml`에 정의된 조치 코드만 사용한다.
- 하나의 문서는 여러 조치의 구체적인 수행 절차를 제공할 수 있다.
- `version`은 문서 내용이나 검색 메타데이터가 변경될 때 함께 변경한다.
- 정책은 필수 조치를 결정하고, 대응 문서는 해당 조치의 수행 방법과 주의사항을 제공한다.

## 내부 정책과 검색 주제 연결

| 정책 `action_code`             | 검색 `topic`                                  |
| ------------------------------ | --------------------------------------------- |
| `VERIFY_CUSTOMER_TRANSACTION`  | `CUSTOMER_CONFIRMATION`                       |
| `URGENT_CUSTOMER_CONFIRMATION` | `CUSTOMER_CONFIRMATION`, `EMERGENCY_RESPONSE` |
| `GUIDE_SECURITY_CHECK`         | `SECURITY_CHECK`                              |
| `REVIEW_RECIPIENT_ACCOUNT`     | `RECIPIENT_ACCOUNT_REVIEW`                    |
| `REVIEW_NEW_RECIPIENT`         | `RECIPIENT_ACCOUNT_REVIEW`                    |
| `REVIEW_ACCOUNT_FLOW`          | `ACCOUNT_FLOW_REVIEW`                         |
| `PRIORITY_ACCOUNT_FLOW_REVIEW` | `ACCOUNT_FLOW_REVIEW`                         |
| `URGENT_ACCOUNT_FLOW_REVIEW`   | `ACCOUNT_FLOW_REVIEW`, `EMERGENCY_RESPONSE`   |
| `ESCALATE_MONITORING_REVIEW`   | `MANUAL_REVIEW`                               |
| `REQUEST_EMERGENCY_REVIEW`     | `MANUAL_REVIEW`, `EMERGENCY_RESPONSE`         |

정책 ID에 특정 문서 ID를 고정하지 않는다. Agent는 사기 유형, 대상 사용자, 대응 주제로
후보 문서를 제한한 뒤 의미 기반 검색을 수행한다.

## 청크 생성 기준

- Front matter는 검색 필터용 메타데이터로 분리한다.
- `##` 제목을 기본 의미 단위로 사용한다.
- 하나의 섹션이 지나치게 길면 문단 경계를 우선하여 추가 분할한다.
- 출처와 문서 성격이 다른 내용을 하나의 청크에 섞지 않는다.
- 구체적인 토큰 크기와 overlap은 후속 검색 평가에서 확정한다.

## 저장 흐름

```text
공식 PDF·웹페이지
→ 출처 확인 및 핵심 내용 요약
→ Markdown 검토 문서
→ 섹션 단위 청크 생성
→ 임베딩 생성
→ DOCUMENTS·DOCUMENT_CHUNKS 저장
→ pgvector 검색
```

## 검증

다음 명령으로 코퍼스 메타데이터를 검증한다.

```powershell
uv run python -m unittest tests.test_agent_guide_corpus -v
```

내부 정책에 실제로 정의된 사기 유형·위험등급·조치 조합마다 검색 가능한
`MONITORING` 대응 문서가 존재하는지는 다음 명령으로 확인한다. 검색과 동일하게
`COMMON` 대상 문서도 커버리지에 포함하며, 누락 조치가 있으면 종료 코드 1을 반환한다.

```powershell
uv run python -m app.scripts.check_agent_guide_coverage
```

현재 기본 코퍼스는 내부 정책 16개에 포함된 조치 32개를 모두 지원한다. 여러 사기
유형에 연결된 공통 긴급대응·체크리스트 문서는 오류로 처리하지 않고 검토 목록으로
함께 출력한다.

## 문서 로더와 청크 생성기

`load_guide_corpus()`는 공식·내부 디렉터리의 Markdown 문서를 정렬된 순서로
읽고 Front Matter 계약, 출처 구분, 문서 ID 중복을 검증한다.
`create_guide_chunks()`는 H1 아래의 소개와 각 H2 섹션을 의미 단위 청크로
변환하며 H3 이하 제목은 상위 섹션의 문맥에 남긴다.

```python
from app.services.agent.guide_corpus import load_and_chunk_guide_corpus

chunks = load_and_chunk_guide_corpus()
```

DB 저장, 임베딩 생성, pgvector 적재는 이 단계에 포함하지 않는다. 로더 출력은
저장 방식과 무관한 불변 도메인 객체이므로 이후 별도 Mapper에서 DB 모델로
변환한다.

다음 명령으로 로더와 청크 생성기까지 함께 검증한다.

```powershell
uv run python -m unittest tests.test_agent_guide_loader tests.test_agent_guide_corpus -v
```

## 검색 평가 세트

`app/resources/agent/guide_retrieval_evaluation.yaml`은 4개 사기 유형별 5개씩,
총 20개의 검색 질문과 기대 문서를 관리한다. 후속 pgvector 검색 구현에서 동일한
평가 세트를 사용하여 Hit Rate@K, MRR, Precision@1을 반복 측정한다.

```powershell
uv run python -m unittest tests.test_agent_guide_evaluation -v
```

## 임베딩 적재와 pgvector 검색

검증된 Markdown 문서는 `text-embedding-3-small`의 1536차원 벡터로 변환하여
`documents`, `document_chunks`에 적재한다. 같은 `document_id`의 내용과 검색
메타데이터가 바뀌면 해당 문서의 Chunk만 교체하며, 변경이 없으면 재임베딩하지 않는다.

```powershell
uv run --env-file .env python -m app.scripts.index_agent_guides
```

검색은 사기 유형, 대상, 위험등급, 조치 코드로 후보 문서를 먼저 제한한 후 pgvector
코사인 유사도로 Top-K Chunk를 반환한다. 다음 명령은 20개 고정 평가 질의로 필터 없는
벡터 검색과 메타데이터 결합 검색의 Precision@1, Hit Rate@3/5, MRR을 비교한다.

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_guide_search `
  --output local_evaluation/guide_search_report.json `
  --csv-output local_evaluation/guide_search_summary.csv
```

### 문서·메타데이터 1차 개선 결과

공통 체크리스트와 플레이북에는 실제 수행 절차를 제공하는 조치 코드만 남기고,
보이스피싱·메신저피싱·사기이용계좌 전문 문서의 절차를 보강했다. 검색 로직과 20개
평가 질의·기대 문서는 변경하지 않고 동일한 기준으로 다시 측정했다.

| 지표        | 개선 전 | 개선 후 |
| ----------- | ------: | ------: |
| Precision@1 |    0.70 |    0.85 |
| Hit Rate@3  |    0.95 |    1.00 |
| Hit Rate@5  |    1.00 |    1.00 |
| MRR         |  0.8292 |  0.9250 |

정책 조치 문서 커버리지는 개선 전후 모두 32/32, 100%를 유지했다. 변경된 문서
7개만 다시 임베딩했으며 기존 문서 9개는 저장된 임베딩을 재사용했다.

외부 API와 PostgreSQL 없이 실행하는 단위 테스트는 다음과 같다.

```powershell
uv run python -m unittest tests.test_agent_guide_vector_search -v
```

## RAG·LLM 대응 계획 품질 평가

`app/resources/agent/response_plan_evaluation.yaml`은 4개 사기 유형과 4개 위험등급의
조합으로 총 16개 평가 시나리오를 관리한다. 동일한 내부 정책을 기준으로
정책-only 계획과 RAG·LLM 계획을 생성하여 다음 지표를 비교한다.

- 필수 조치 포함률
- 허용된 조치 코드 정확도
- 조치별 수행 절차 생성률
- 조치별 주의사항 생성률
- 출력 계약 준수율
- fallback 발생률
- RAG 전체 검색시간
- 생성·전체 처리시간의 평균, P50, P95

단위 테스트는 Fake 검색기와 Fake 생성기를 사용하므로 PostgreSQL과 OpenAI API를
호출하지 않는다.

```powershell
uv run python -m unittest tests.test_agent_response_plan_evaluation -v
```

실제 pgvector와 OpenAI 모델을 사용한 평가는 대응 가이드 적재 후 다음 명령으로
명시적으로 실행한다. `--output`을 생략하면 결과를 터미널에만 출력한다.

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_response_plans `
  --repeat 3 `
  --top-k 3 `
  --output local_evaluation/response_plan_report.json `
  --csv-output local_evaluation/response_plan_results.csv
```

품질 평가는 기본적으로 대응 계획 캐시를 비활성화한다. 따라서 동일한 Golden Set을
반복 실행해도 실제 LLM 생성 품질과 지연시간을 측정한다. 캐시 성능을 별도로 확인할
때만 `--cache-size 64`처럼 명시적으로 설정한다.

특정 시나리오만 빠르게 연결 확인하려면 `--case-id`를 사용한다. 이 옵션은 전체
기준값을 대체하지 않고, pgvector·LLM·CSV 저장 경로의 스모크 테스트에 사용한다.

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_response_plans `
  --case-id PLAN-TAKEOVER-HIGH `
  --output local_evaluation/response_plan_smoke_report.json `
  --csv-output local_evaluation/response_plan_smoke_results.csv
```

### 1차 실제 생성 평가 결과

2026-08-15에 `gpt-5-mini`, 30초 호출 제한, 유형별 `HIGH`·`VERY_HIGH` 총
8개 시나리오로 1회 측정한 확장 전 기준 결과이다. 이후에는 16개 Golden Set과
캐시 미사용 조건에서 반복 측정하여 최종 발표 지표를 확정한다.

| 지표                  | 정책-only |     RAG·LLM |
| --------------------- | --------: | ----------: |
| 필수 조치 포함률      |     1.000 |       1.000 |
| 허용 조치 코드 정확도 |     1.000 |       1.000 |
| 수행 절차 생성률      |     0.000 |       0.875 |
| 주의사항 생성률       |     0.000 |       0.875 |
| 출력 계약 준수율      |     1.000 |       1.000 |
| fallback률            |     0.000 |       0.125 |
| 평균 검색시간         |       0ms |    682.62ms |
| 평균 생성시간         |       0ms | 26,484.38ms |

RAG·LLM 8건 중 7건은 모든 정책 조치의 수행 절차와 주의사항을 생성했다. 보이스피싱
`VERY_HIGH` 1건은 30초 제한시간에 도달하여 정책-only 계획으로 안전하게
fallback되었다. 정책 조치 코드는 모든 결과에서 그대로 유지되어 LLM이 사기 유형과
필수 조치를 변경하지 못하도록 한 가드레일이 정상 작동했다.

### 생성 지연시간 개선 결과

1차 평가에서 확인된 평균 26.48초의 생성 지연과 12.5% fallback을 비교하기 위해
검색 문맥 Top-3, `gpt-5-mini` reasoning effort `low`, 최대 생성 토큰 3000인 후보를
8개 시나리오에서 3회씩 총 24건 실행했다. 아래 표는 이 **평가 후보의 과거 측정값**이다.

| 지표                  |     개선 전 |     개선 후 |
| --------------------- | ----------: | ----------: |
| 평가 건수             |         8건 |        24건 |
| 필수 조치 포함률      |       1.000 |       1.000 |
| 허용 조치 코드 정확도 |       1.000 |       1.000 |
| 수행 절차 생성률      |       0.875 |       1.000 |
| 주의사항 생성률       |       0.875 |       1.000 |
| 출력 계약 준수율      |       1.000 |       1.000 |
| fallback률            |       0.125 |       0.000 |
| 평균 검색시간         |    682.62ms |    558.00ms |
| 평균 생성시간         | 26,484.38ms | 12,844.42ms |
| 생성시간 P50          |      미측정 |    12,419ms |
| 생성시간 P95          |      미측정 |    15,804ms |

평균 생성시간은 약 51.5% 감소했으며, 24건 모두 정책 조치와 출력 계약을 유지했다.
이 측정에서는 출력 토큰을 1200으로 제한한 후보가 모든 시나리오에서 구조화 출력 생성에
실패했다.

현재 운영 코드의 기본값은 이 측정 조건과 다르다.
[response_plan_generator.py](../../app/services/agent/response_plan_generator.py)는
모델을 `AGENT_RESPONSE_PLAN_MODEL` → `OPENAI_MODEL` → `gpt-5` 순서로 선택하고,
`OPENAI_TIMEOUT_SECONDS = 15`, `OPENAI_MAX_RETRIES = 0`,
`RESPONSE_PLAN_REASONING_EFFORT = "low"`, `RESPONSE_PLAN_MAX_COMPLETION_TOKENS = 1200`을
기본으로 사용한다. [workflow.py](../../app/services/agent/workflow.py)는 검색 `top_k = 3`을 전달한다.
따라서 3000 토큰 결과를 다시 측정하려면 아래처럼 옵션을 명시해야 한다.

반복 횟수와 검색 문서 수를 변경하여 재측정할 수 있다.

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_response_plans `
  --repeat 3 `
  --top-k 3 `
  --reasoning-effort low `
  --max-completion-tokens 3000 `
  --output local_evaluation/response_plan_report.json `
  --csv-output local_evaluation/response_plan_results.csv
```
