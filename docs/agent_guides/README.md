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

## 내부 정책과 검색 주제 연결

| 정책 `action_code` | 검색 `topic` |
|---|---|
| `VERIFY_CUSTOMER_TRANSACTION` | `CUSTOMER_CONFIRMATION` |
| `URGENT_CUSTOMER_CONFIRMATION` | `CUSTOMER_CONFIRMATION`, `EMERGENCY_RESPONSE` |
| `GUIDE_SECURITY_CHECK` | `SECURITY_CHECK` |
| `REVIEW_RECIPIENT_ACCOUNT` | `RECIPIENT_ACCOUNT_REVIEW` |
| `REVIEW_NEW_RECIPIENT` | `RECIPIENT_ACCOUNT_REVIEW` |
| `REVIEW_ACCOUNT_FLOW` | `ACCOUNT_FLOW_REVIEW` |
| `PRIORITY_ACCOUNT_FLOW_REVIEW` | `ACCOUNT_FLOW_REVIEW` |
| `URGENT_ACCOUNT_FLOW_REVIEW` | `ACCOUNT_FLOW_REVIEW`, `EMERGENCY_RESPONSE` |
| `ESCALATE_MONITORING_REVIEW` | `MANUAL_REVIEW` |
| `REQUEST_EMERGENCY_REVIEW` | `MANUAL_REVIEW`, `EMERGENCY_RESPONSE` |

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

