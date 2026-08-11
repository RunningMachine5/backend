---
document_id: FDS-INTERNAL-MONITORING-PLAYBOOK-001
title: FDShield 사기 유형별 모니터링 대응 절차
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

# FDShield 사기 유형별 모니터링 대응 절차

> 이 문서는 FDShield 팀 프로젝트 시연을 위해 작성한 가상의 내부 절차이다.
> 실제 금융회사의 승인된 정책이나 금융당국의 공식 지침이 아니다.

## 공통 처리 원칙

1. ML·Rule·위험등급 결과를 변경하지 않고 원본 판정과 적중 근거를 확인한다.
2. 내부 대응 정책에서 필수 조치와 체크리스트를 조회한다.
3. 공식 대응 문서를 검색하여 조치의 근거와 고객 안내 내용을 보강한다.
4. Agent가 생성한 권고와 담당자가 실제 수행한 조치를 구분한다.
5. 지급정지나 계좌 제한처럼 외부 상태를 변경하는 조치는 Agent가 직접 실행하지 않는다.

## 보이스피싱

- 사칭 대상과 거래 목적을 확인한다.
- 고객의 본인 거래 여부와 추가 송금 여부를 확인할 항목으로 제시한다.
- 수취계좌의 위험정보와 동일 계좌로 이어진 추가 거래를 담당자 검토 대상으로 제시한다.
- 피해가 확인되면 공식 피해 신고·구제 절차 문서를 우선 안내한다.

## 메신저피싱

- 가족·지인 사칭 정황과 별도 연락수단을 통한 본인 확인 여부를 점검한다.
- 신규 수취계좌 또는 과거 거래가 없는 계좌인지 확인할 항목으로 제시한다.
- 메시지, 송금 내역 등 사건 확인자료 보존을 권고한다.

## 계정탈취

- 악성 앱, 원격제어, 인증 변경 등 실제 적중한 Rule 근거를 확인한다.
- 고객 거래 진위와 탐지 이후 추가 거래를 확인할 항목으로 제시한다.
- 보안 점검 안내는 금융보안원 등 공식 위협 자료와 고객용 대응 문서를 함께 사용한다.

## 사기이용계좌

- 자금 유입·유출 흐름과 연결 수취계좌를 담당자 검토 대상으로 제시한다.
- 정상적인 거래 당사자가 의도치 않게 범죄자금 흐름에 포함될 가능성을 함께 고려한다.
- Agent가 계좌 명의인의 고의 가담 여부를 자동 확정하지 않도록 한다.
- 계좌 제한 조치는 담당자와 소속 금융회사의 승인 절차에서 판단하도록 한다.

## 대응 결과 작성 기준

- 권장 조치마다 적용 정책과 참고 문서를 구분한다.
- 확인되지 않은 사실을 확정된 범죄 사실처럼 작성하지 않는다.
- 정보가 부족하면 부족한 항목과 담당자 확인 필요사항을 명시한다.

