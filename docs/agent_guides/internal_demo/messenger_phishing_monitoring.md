---
document_id: FDS-INTERNAL-MESSENGER-MONITORING-001
title: FDShield 메신저피싱 모니터링 대응 절차
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - MESSENGER_PHISHING
audiences:
  - MONITORING
topics:
  - MESSENGER_IDENTITY_CHECK
  - CUSTOMER_CONFIRMATION
  - RECIPIENT_ACCOUNT_REVIEW
  - ADDITIONAL_TRANSACTION_REVIEW
risk_grades:
  - LOW
  - MEDIUM
  - HIGH
  - VERY_HIGH
action_codes:
  - REVIEW_MESSENGER_EVIDENCE
  - VERIFY_MESSENGER_CONTEXT
  - REVIEW_NEW_RECIPIENT
  - VERIFY_CUSTOMER_TRANSACTION
  - GUIDE_SEPARATE_CONTACT_CHECK
  - URGENT_CUSTOMER_CONFIRMATION
  - REQUEST_EMERGENCY_REVIEW
  - GUIDE_MESSENGER_PHISHING_RESPONSE
version: "1.1"
published_at: 2026-08-11
accessed_at: 2026-08-11
---

# FDShield 메신저피싱 모니터링 대응 절차

> FDShield 팀 프로젝트 시연을 위한 가상의 내부 절차이며 실제 금융회사의 승인된 정책이 아니다.

## 사칭 정황 확인

가족·지인의 긴급 요청, 휴대전화 고장이나 번호 변경 주장, 대신 결제·송금 요청 등 사건에
실제로 확인된 정황을 구분한다. 고령자 모바일 환경만으로 메신저피싱을 확정하지 않는다.

## 별도 연락수단 확인

메신저 대화창에 포함된 연락처가 아닌 기존에 알고 있던 전화번호로 사칭 대상자의 신원을
확인하도록 안내한다. 확인 전 추가 송금이나 개인정보 전달을 중단하도록 권고한다.

## 수취계좌 검토

신규 수취계좌, 과거 거래가 없는 계좌, 짧은 시간의 반복 송금 여부를 검토한다. 메시지 내용,
송금 내역, 상대방 계정 등 담당자 판단에 필요한 자료 보존 여부를 체크리스트에 포함한다.

## 긴급 고객 확인

VERY_HIGH 사건은 등록된 고객 연락처로 거래 일시·금액·수취계좌의 본인 인지 여부를 우선
확인한다. 메신저에서 전달받은 연락처를 사용하지 않고, 사칭된 가족이나 지인에게 기존에
알고 있던 전화번호로 별도 확인했는지 질문한다. 확인 전에는 추가 송금과 개인정보 전달을
중단하도록 안내하고, 확인 결과와 추가 거래 발생 여부를 담당자 검토 기록에 남긴다.

## 위험등급별 적용

LOW·MEDIUM은 사칭 정황과 신규 수취계좌 확인을 중심으로 한다. HIGH·VERY_HIGH는 고객 거래
진위 확인, 별도 연락수단 확인, 추가 거래 검토와 피해 대응 안내를 우선한다.
