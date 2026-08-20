---
document_id: FDS-INTERNAL-FRAUD-ACCOUNT-MONITORING-001
title: FDShield 사기이용계좌 모니터링 대응 절차
source_type: INTERNAL_DEMO_GUIDE
source_name: FDShield 시연용 내부 지침
source_url: null
fraud_types:
  - FRAUD_USED_ACCOUNT
audiences:
  - MONITORING
topics:
  - ACCOUNT_FLOW_REVIEW
  - RECIPIENT_ACCOUNT_REVIEW
  - MANUAL_REVIEW
  - EMERGENCY_RESPONSE
risk_grades:
  - LOW
  - MEDIUM
  - HIGH
  - VERY_HIGH
action_codes:
  - REVIEW_FRAUD_ACCOUNT_EVIDENCE
  - REVIEW_ACCOUNT_FLOW
  - REVIEW_LINKED_ACCOUNTS
  - PRIORITY_ACCOUNT_FLOW_REVIEW
  - REQUEST_ACCOUNT_RISK_REVIEW
  - URGENT_ACCOUNT_FLOW_REVIEW
  - REQUEST_SUSPENSION_REVIEW
  - PRESERVE_CASE_EVIDENCE
version: "1.2"
published_at: 2026-08-11
accessed_at: 2026-08-11
---

# FDShield 사기이용계좌 모니터링 대응 절차

> FDShield 팀 프로젝트 시연을 위한 가상의 내부 절차이며 실제 금융회사의 승인된 정책이 아니다.

## 자금 흐름 근거 확인

다수 계좌에서의 자금 유입, 단시간 유입·유출, 여러 계좌로의 분산, 입금 후 현금화 등 실제로
계산된 Rule 근거를 확인한다. 현재 데이터로 확인할 수 없는 자금 흐름은 추정해 작성하지 않는다.

## Rule 근거별 확인 절차

출금계좌와 수취계좌가 모두 거래 제한 상태이면 제한 상태와 변경 시각을 먼저 확인한다. 출금계좌
정지 해제만 확인된 경우와 수취계좌 정지만 확인된 경우를 구분하고, 다른 자금 흐름 근거 없이
제한 상태 하나만으로 사기이용계좌를 확정하지 않는다.

거래 재개 이후 고액 입금이 확인되면 재개 시각과 입금 시각을 비교한다. 고액 입금과 단시간
반복송금이 함께 적중하면 유입금액, 반복 횟수, 수취계좌 수를 검토한다. 단시간 반복송금만
적중한 경우에는 정상적인 급여·정산·사업 거래 가능성을 반대 근거로 함께 확인한다.

## 연결계좌 검토

수취계좌의 위험상태, 과거 거래관계, 연결된 거래의 시간과 금액을 검토한다. 현재 사건의
계좌 명의인이 고의로 가담했다고 자동 판단하지 않고 정상 거래 문맥의 가능성을 함께 기록한다.

## 담당자 계좌 위험 검토

담당자는 Rule 점수만으로 사기이용계좌를 확정하지 않는다. 거래 상대방과 실제 입금자·물품
전달 대상이 일치하는지, 과거 정상 거래관계가 있었는지, 단시간 유입·유출이 반복됐는지를
함께 확인한다. 정상 거래 자료가 확인되면 반대 근거로 기록하고, 정보가 상충하거나 부족하면
추가 검토가 필요하다고 표시한다.

## 고위험 사건 처리

HIGH는 자금 흐름과 연결계좌를 우선 검토하고, VERY_HIGH는 추가 이동 가능성과 계좌 제한 검토
필요성을 담당자에게 알린다. 지급정지나 거래 제한은 승인된 절차에 따라 사람이 결정한다.

## 근거 보존

Rule 유형별 점수, 적중 구성요소, 거래 식별자, 검토 당시의 자금 흐름 정보를 보존하여 담당자
판정과 후속 조사에서 동일한 입력을 확인할 수 있도록 한다.
