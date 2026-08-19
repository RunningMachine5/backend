---
document_id: FDS-INTERNAL-VOICE-MONITORING-001
title: FDShield 보이스피싱 모니터링 대응 절차
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - VOICE_PHISHING
audiences:
  - MONITORING
topics:
  - CUSTOMER_CONFIRMATION
  - RECIPIENT_ACCOUNT_REVIEW
  - ADDITIONAL_TRANSACTION_REVIEW
  - MANUAL_REVIEW
risk_grades:
  - LOW
  - MEDIUM
  - HIGH
  - VERY_HIGH
action_codes:
  - REVIEW_VOICE_PHISHING_EVIDENCE
  - VERIFY_VOICE_PHISHING_CONTEXT
  - REVIEW_RECIPIENT_ACCOUNT
  - VERIFY_CUSTOMER_TRANSACTION
  - ESCALATE_MONITORING_REVIEW
  - URGENT_CUSTOMER_CONFIRMATION
  - REQUEST_EMERGENCY_REVIEW
  - GUIDE_VOICE_PHISHING_RESPONSE
version: "1.2"
published_at: 2026-08-11
accessed_at: 2026-08-11
---

# FDShield 보이스피싱 모니터링 대응 절차

> FDShield 팀 프로젝트 시연을 위한 가상의 내부 절차이며 실제 금융회사의 승인된 정책이 아니다.

## 판정 근거 확인

유형별 Rule 점수와 Agent 적용 유형을 확인하고 기관·대출 사칭, 고액 송금, 신규 수취계좌 등
실제로 적중한 근거만 사건 요약에 사용한다. 고객의 의도나 범죄 피해 여부를 Rule 결과만으로
확정하지 않는다.

## 고객 거래 확인

등록된 고객 연락처를 이용해 거래일시, 금액, 수취계좌의 본인 인지 여부를 확인한다.
수사기관이나 금융회사를 사칭한 연락을 받았는지 질문하되 비밀번호, OTP, 인증번호는
요구하지 않는다. 추가 송금 요구가 있는지도 확인항목으로 제시한다.

## 수취계좌 및 후속 거래 검토 절차

1. 수취계좌가 신규 등록 계좌인지 확인한다.
2. 고객과 해당 수취계좌의 과거 거래 횟수와 거래 이력을 확인한다.
3. 같은 수취계좌로 짧은 시간 안에 반복 송금됐는지 확인한다.
4. 탐지 이후 추가 고액송금 또는 다른 수취계좌로의 송금이 발생했는지 확인한다.

지급정지나 계좌 제한은 Agent가 결정하지 않는다. 담당자가 수취계좌 위험정보와 공식
피해구제 안내를 확인하고 승인된 내부 절차에 따라 후속 조치를 판단한다.

## 위험등급별 적용

LOW·MEDIUM은 적중 근거와 거래 문맥 확인을 중심으로 구성한다. HIGH는 고객 확인과 우선 검토를
포함하고, VERY_HIGH는 긴급 고객 확인과 피해 대응 안내 준비를 우선순위로 제시한다.
