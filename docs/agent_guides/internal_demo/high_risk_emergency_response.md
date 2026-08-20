---
document_id: FDS-INTERNAL-HIGH-RISK-EMERGENCY-001
title: FDShield HIGH·VERY_HIGH 이상거래 긴급 우선 확인 절차
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
  - CUSTOMER
topics:
  - EMERGENCY_RESPONSE
  - CUSTOMER_CONFIRMATION
  - MANUAL_REVIEW
risk_grades:
  - HIGH
  - VERY_HIGH
action_codes:
  - URGENT_CUSTOMER_CONFIRMATION
  - REQUEST_EMERGENCY_REVIEW
  - URGENT_ACCOUNT_FLOW_REVIEW
  - PRESERVE_CASE_EVIDENCE
version: "1.3"
published_at: 2026-08-11
accessed_at: 2026-08-11
---

# FDShield 고위험 이상거래 긴급 대응 절차

> FDShield 팀 프로젝트 시연을 위한 가상의 내부 절차이며 실제 금융회사의 승인된 정책이 아니다.

## 우선 확인

거래 식별자, 탐지 시각, 위험등급, 유형별 Rule 점수와 Agent 적용 유형, 적중 근거를 확인한다. 고객 연락이 필요한
경우 등록된 공식 연락처를 사용하고 추가 거래가 진행 중인지 우선 확인한다.

## 유형별 긴급 확인

보이스피싱은 금융회사·수사기관·대출상담사 사칭 연락과 추가 송금 요구를 확인한다.
메신저피싱은 가족·지인 사칭 메시지와 별도 연락수단을 통한 신원 확인 여부를 확인한다.
계정탈취는 고객 거래 진위와 단말·인증 변경 및 악성 앱 정황을 함께 확인한다.
사기이용계좌는 계좌 제한 상태, 입금 후 신속한 유출, 연결계좌의 자금 흐름을 우선 검토한다.

## VERY_HIGH 메신저피싱 우선 확인 항목

매우 높은 메신저피싱 위험 사건에서는 일반적인 사칭 정황 확인보다 고객 거래 진위를 먼저
확인한다. 등록된 고객 연락처로 거래 일시·금액·수취계좌를 본인이 인지하는지 확인하고,
가족·지인 사칭 메시지의 상대방을 기존 전화번호 등 별도 연락수단으로 확인했는지 질문한다.
추가 송금·개인정보 전달·원격제어 앱 설치가 진행 중인지도 함께 확인한 뒤, 담당자 긴급 검토를
요청한다.

어느 유형이든 등록된 공식 고객 연락처로 거래일시·금액·수취계좌를 확인하고, 탐지 결과에
포함되지 않은 정황을 추정해 안내하지 않는다.

## 유형별 긴급 조치 방향

고객 확인은 권고 조치이며 담당자의 확인 없이 사기 피해를 확정하지 않는다. 위험등급이 높더라도
계좌 제한, 거래 보류, 지급정지 같은 외부 상태 변경은 별도 승인 절차에 따라 수행한다.

## 담당자 검토 요청

Agent가 제시한 권장 조치와 실제 실행 권한을 구분한다. 계좌 제한, 거래 보류, 지급정지 등 외부
상태를 변경하는 조치는 담당자가 승인된 내부 절차에 따라 판단한다.

## 근거와 처리 결과 보존

Agent 입력, Rule 근거, 참고 문서, 권장 조치와 담당자가 실제 수행한 조치를 구분해 기록한다.
확인되지 않은 정보를 채우기 위해 추정하지 않고 미확인 항목으로 남긴다.
