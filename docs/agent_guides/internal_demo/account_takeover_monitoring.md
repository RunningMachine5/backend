---
document_id: FDS-INTERNAL-ACCOUNT-TAKEOVER-MONITORING-001
title: FDShield 계정탈취 모니터링 대응 절차
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - ACCOUNT_TAKEOVER
audiences:
  - MONITORING
topics:
  - SECURITY_CHECK
  - CUSTOMER_CONFIRMATION
  - ADDITIONAL_TRANSACTION_REVIEW
  - MANUAL_REVIEW
risk_grades:
  - LOW
  - MEDIUM
  - HIGH
  - VERY_HIGH
action_codes:
  - REVIEW_ACCOUNT_TAKEOVER_EVIDENCE
  - VERIFY_ACCOUNT_ACCESS_CONTEXT
  - REVIEW_TRANSACTION_CONTEXT
  - VERIFY_CUSTOMER_TRANSACTION
  - GUIDE_SECURITY_CHECK
  - URGENT_CUSTOMER_CONFIRMATION
  - REQUEST_EMERGENCY_REVIEW
version: "1.2"
published_at: 2026-08-11
accessed_at: 2026-08-11
---

# FDShield 계정탈취 모니터링 대응 절차

> FDShield 팀 프로젝트 시연을 위한 가상의 내부 절차이며 실제 금융회사의 승인된 정책이 아니다.

## 단말·인증 근거 확인

원격제어, 루팅·탈옥, 인증수단 변경, 비정상 접속 실패 등 실제 Rule 적중 근거를 확인한다.
탐지 시점에 알 수 없는 사후 정보는 대응 근거로 사용하지 않는다.

## Rule 근거별 확인 절차

미사용 단말과 단말 이상 정황이 함께 적중하면 과거 사용 단말과 현재 단말의 차이를 확인한다.
복수의 단말 이상행동이 적중하면 적중한 항목을 개별적으로 제시하고 하나의 악성행동으로
합쳐 단정하지 않는다. 인증정보 변경과 단말 이상이 함께 확인되면 변경 시각과 거래 발생
순서를 확인한다.

직전 거래지와의 이동이 현실적으로 어렵고 VPN·로밍까지 함께 확인되면 접속 위치와 시각을
우선 검토한다. 접속 실패 횟수는 계정 접근 이상을 보강하는 근거로만 사용하며, 실패 횟수만으로
계정탈취를 확정하지 않는다.

## 고객 거래 확인

등록된 연락처를 이용해 거래의 본인 수행 여부를 확인한다. 고객이 원격제어 앱이나 출처가
불명확한 앱을 설치했는지 확인하되 앱 이름 하나만으로 악성 여부를 단정하지 않는다.

## 보안 점검 안내

의심 앱의 추가 실행을 중단하고 금융 앱 인증정보와 비밀번호를 안전한 환경에서 점검하도록
안내한다. 구체적인 삭제·신고 절차는 공식 보안 자료와 금융회사 최신 절차를 함께 확인한다.

## 추가 거래 검토

탐지 이후 발생한 추가 고액송금과 수취계좌 변화를 확인한다. Agent는 계정 잠금이나 거래 제한을
직접 실행하지 않고 담당자의 승인 대상 조치로 제시한다.

## HIGH·VERY_HIGH 우선순위

HIGH는 고객 거래 확인과 단말·인증 정황 검토를 함께 수행한다. VERY_HIGH는 고객 확인,
추가 거래 검토, 안전한 단말에서의 인증정보 점검 안내를 우선 제시한다. 계정 잠금이나 거래
제한은 Agent가 자동 실행하지 않는다.

## VERY_HIGH 계정탈취 추가 거래 확인

매우 높은 계정탈취 위험에서는 탐지 이후의 추가 거래를 우선 확인한다. 현재 거래 이후의
고액 이체, 새 수취계좌 등록·변경, 동일 단말 또는 새로운 단말의 접속 시도와 인증정보 변경을
함께 검토한다. 추가 거래가 확인되면 고객 거래 진위와 단말 보안 상태를 확인하도록 담당자
긴급 검토를 요청하며, 계정 잠금이나 거래 제한은 자동으로 실행하지 않는다.
