---
document_id: FSI-BLACKECHO-2024-001
title: 금융·백신 앱 위장 보이스피싱 악성 앱 분석 요약
source_type: OFFICIAL_GUIDE
source_name: 금융보안원
source_url: https://www.fsec.or.kr/bbs/detail?menuNo=244&bbsNo=11611
fraud_types:
  - VOICE_PHISHING
  - ACCOUNT_TAKEOVER
audiences:
  - MONITORING
topics:
  - SECURITY_CHECK
  - CUSTOMER_CONFIRMATION
  - MANUAL_REVIEW
risk_grades:
  - MEDIUM
  - HIGH
  - VERY_HIGH
action_codes:
  - VERIFY_ACCOUNT_ACCESS_CONTEXT
  - VERIFY_CUSTOMER_TRANSACTION
  - GUIDE_SECURITY_CHECK
version: "1.0"
published_at: 2024-12-31
accessed_at: 2026-08-09
---

# 금융·백신 앱 위장 보이스피싱 악성 앱 분석 요약

## 분석 대상

금융보안원은 대출을 빙자해 피해자를 유인한 뒤 금융 앱 또는 백신 앱으로 위장한
악성 앱을 유포하는 공격을 `Operation BlackEcho`로 분석했다. 2023년 7월부터 약 1년간
관련 악성 앱 약 900개를 수집·분석한 결과를 기반으로 한다.

## 확인된 공격 특징

- 금융 관련 앱으로 위장한 1차 악성 앱이 추가 악성 앱 설치로 이어질 수 있다.
- 추가 앱은 백신 등 신뢰할 만한 보안 프로그램으로 위장할 수 있다.
- 악성 앱의 기능과 공격 단계가 세분화되고 은닉·자동화되는 경향이 확인되었다.
- 단일 앱 이름이나 한 가지 설치 경로만 확인하는 방식으로는 변형 공격을 놓칠 수 있다.

## 모니터링 시 참고할 관점

다음 항목은 보고서의 공격 특징을 FDShield 모니터링 문맥에 적용한 참고사항이다.

- 악성 앱이나 원격제어 정황이 Rule 근거에 포함되었는지 확인한다.
- 대출빙자 정황과 단말 악성행동이 함께 탐지되면 계정탈취 가능성을 우선 검토한다.
- 앱 이름만으로 정상 여부를 판단하지 않고 설치 경로와 추가 앱 정황을 함께 확인한다.

## 문서 사용 시 주의사항

이 문서는 위협 인텔리전스 보고서의 공격 특성을 요약한 것이며 고객 피해구제 절차를
정한 문서가 아니다. 고객 안내에는 별도의 공식 피해구제 자료를 함께 사용해야 한다.

## 출처

- [금융보안원 Operation BlackEcho 인텔리전스 보고서 안내](https://www.fsec.or.kr/bbs/detail?menuNo=244&bbsNo=11611)

