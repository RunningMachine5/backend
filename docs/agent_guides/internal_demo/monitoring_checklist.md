---
document_id: FDS-INTERNAL-MONITORING-CHECKLIST-001
title: FDShield 모니터링 담당자 공통 체크리스트
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - VOICE_PHISHING
  - MESSENGER_PHISHING
  - ACCOUNT_TAKEOVER
  - FRAUD_USED_ACCOUNT
audiences:
  - MONITORING
topics:
  - CUSTOMER_CONFIRMATION
  - SECURITY_CHECK
  - RECIPIENT_ACCOUNT_REVIEW
  - ADDITIONAL_TRANSACTION_REVIEW
  - ACCOUNT_FLOW_REVIEW
  - MANUAL_REVIEW
published_at: 2026-08-09
accessed_at: 2026-08-09
---

# FDShield 모니터링 담당자 공통 체크리스트

> 이 문서는 FDShield 팀 프로젝트 시연을 위해 작성한 가상의 체크리스트이다.
> 실제 금융회사의 승인된 내부 통제 기준이 아니다.

## 판정 결과 확인

- [ ] 원본 거래 식별자와 탐지 시각을 확인한다.
- [ ] Rule 대표 유형과 전체 유형별 점수를 확인한다.
- [ ] 실제 적중한 Rule 근거를 확인한다.
- [ ] 위험점수와 위험등급을 확인한다.
- [ ] 유형이 애매한 경우 Agent의 유사 사건 조사 근거를 확인한다.

## 고객 확인

- [ ] 고객 본인 거래 여부 확인이 필요한 사건인지 확인한다.
- [ ] 추가 송금 또는 후속 거래가 존재하는지 확인한다.
- [ ] 사칭 대상자에게 별도 연락수단으로 확인했는지 점검한다.

## 단말·보안 확인

- [ ] 악성 앱 또는 원격제어 관련 Rule 근거가 적중했는지 확인한다.
- [ ] 인증정보 변경 정황이 적중했는지 확인한다.
- [ ] 고객에게 제공할 보안 점검 문서의 출처를 확인한다.

## 계좌·자금 흐름 확인

- [ ] 신규 또는 과거 거래가 없는 수취계좌인지 확인한다.
- [ ] 수취계좌 위험정보를 확인한다.
- [ ] 단시간 자금 유입·유출과 연결계좌를 확인한다.
- [ ] 정상 거래 당사자가 의도치 않게 연루될 가능성을 검토한다.

## 대응 계획 확인

- [ ] 내부 정책의 필수 조치가 모두 포함되었는지 확인한다.
- [ ] 대응 가이드의 공식 출처와 시연용 내부 문서가 구분되었는지 확인한다.
- [ ] 확인되지 않은 사실이 확정적으로 작성되지 않았는지 확인한다.
- [ ] 외부 상태 변경 조치가 Agent에 의해 자동 실행되지 않았는지 확인한다.

