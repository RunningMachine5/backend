# 고객 대응 챗봇 — LLM 프롬프트

[고객 대응 챗봇 설계 (PRD)](customer-chatbot.md)의 부속 문서다.
챗봇이 LLM에 보내는 프롬프트 전문을 모아둔다.
흐름과 분기 조건은 PRD의 [2. 작동 시나리오](customer-chatbot.md#2-작동-시나리오)에 있다.

고객에게 출력하는 안내 문구는 이 문서가 아니라
[고객 안내 문구](customer-chatbot-messages.md)에 있다.

| 절 | 프롬프트 | 사용처 |
| --- | --- | --- |
| [A.1](#a1-고객응답-평가-프롬프트) | 고객응답 평가 | 응답 충실도 판정 (`SUFFICIENT` 외 4종) |
| [A.2](#a2-고객-행동-추출-프롬프트) | 고객 행동 추출 | `customer_action` 19종 |
| [A.3](#a3-사기-정황-추출-프롬프트) | 사기 정황 추출 | `fraud_circumstance` 20종 |

> `type` 필드의 허용값은 프롬프트가 아니라 파이썬 코드로 강제한다.
> PRD [DB·스키마 3.8](customer-chatbot-schema.md#38-appdomain-enum-코드-상수화)를 따른다.

---

### A.1 고객응답 평가 프롬프트

사용처: [2.4 조건 2](customer-chatbot.md#조건-2-고객응답-평가-llm)

```text
QUALITY_CHECK_PROMPT = """
당신은 금융 이상거래 상담 챗봇에서 고객 응답의 충실도를 평가합니다.

직전 질문: {question_text}
이 질문의 목적: {target_hint}
고객 응답: {customer_answer}
직전 대화 맥락: {conversation_context}

다음 중 하나로 분류하세요.

- SUFFICIENT: 질문 목적에 맞는 구체적 사실이 확인됨
- TOO_VAGUE: 답변은 했으나 목적 정보를 특정할 수 없을 만큼 모호함
- NON_ANSWER: 질문과 무관하거나 판단 불가한 응답
- REFUSAL: 답변을 명시적으로 거부하거나 회피 의사를 밝힘
- WANT_END: 상담을 종료하기를 원함

규칙:

- "모름", "기억 안 남"은 NON_ANSWER로 분류합니다.
- 고객이 되묻는 경우, 질문 목적과 관련된 되물음이면 NON_ANSWER,
  회피성 되물음("그건 왜 물어봐요?")이면 REFUSAL로 분류합니다.
- 침묵/무응답은 이 노드에 들어오지 않으므로 고려하지 않습니다.

{few_shot_examples}

출력은 JSON만 반환하세요.
"""
```

### A.2 고객 행동 추출 프롬프트

사용처: [2.5 고객 행동 추출](customer-chatbot.md#고객-행동-추출) · `customer_action` 19종

```text
당신은 금융 이상거래 상담에서 고객이 실제로 수행한 행동을 추출하는 분류기입니다.

다음 규칙을 따르세요.

- 사용자가 실제로 했다고 명확하게 말한 행동만 추출합니다.
- 상대방에게 요청만 받은 행동, 하지 않았다고 부정한 행동, 언급하지 않은 행동은 추출하지 않습니다.
- 거래 성공 여부만으로 고객이 직접 실행하거나 승인했다고 추정하지 않습니다.
- 고객이 거래를 직접 입력하고 실행했다면 detected_transaction_initiated입니다.
- 다른 사람이나 시스템이 준비한 거래를 고객이 인증·승인만 했다면 detected_transaction_approved입니다.
- 위 두 항목을 같은 거래에 동시에 적용하지 않습니다.
- 탐지된 거래와 관련 없는 과거 행동은 추출하지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- 모호하거나 충돌하는 내용은 추출하지 않습니다.
- 사용자가 이전 답변을 정정하면 최신 답변을 따릅니다.
- 허용된 enum 이외의 값은 생성하지 않습니다.

customer_action 정의:

detected_transaction_initiated
: 고객이 탐지된 거래를 직접 입력하고 실행함

detected_transaction_approved
: 다른 사람이 준비한 탐지 거래를 고객이 인증하거나 승인함

cash_delivered_after_withdrawal
: 현금을 출금한 뒤 다른 사람에게 직접 전달함

received_funds_forwarded
: 입금받은 돈을 다른 계좌나 사람에게 다시 송금함

received_funds_withdrawn
: 입금받은 돈을 현금으로 출금함

goods_or_asset_delivered_for_payment
: 입금의 대가로 물품·금·외화 등 자산을 전달함

bank_account_rented_or_transferred
: 본인 명의 계좌를 다른 사람에게 대여하거나 양도함

account_access_or_payment_instrument_shared
: 금융계정 접근정보·통장·카드·OTP 기기 등을 다른 사람에게 전달함

phishing_link_opened
: 상대방이 보낸 의심스러운 링크를 열거나 누름

financial_credentials_entered_or_shared
: 금융서비스 아이디·비밀번호·PIN을 입력하거나 전달함

otp_or_authentication_code_shared
: OTP·문자·ARS 등의 인증번호를 입력하거나 전달함

identity_document_shared
: 신분증 사진·사본·위임장 등을 전달함

card_information_shared
: 카드번호·유효기간·CVC·카드 비밀번호를 전달함

suspicious_app_installed
: 상대방이 안내한 앱이나 APK를 설치함

remote_control_or_security_permission_granted
: 원격제어·접근성·기기관리자 등의 권한을 허용함

loan_taken_for_transaction
: 탐지 거래의 자금을 마련하기 위해 대출을 실행함

account_opened_for_other_party
: 상대방 요청에 따라 계좌를 개설하거나 사용하게 함

open_banking_or_external_finance_linked
: 상대방 요청에 따라 오픈뱅킹이나 외부 금융서비스를 연결함

crypto_purchased_or_transferred
: 탐지 거래와 관련해 가상자산을 구매하거나 외부 지갑으로 전송함

거래 정보:
{{transaction_context}}

사용자 답변:
{{user_answers}}

출력 형식:
{
  "customer_actions": [
    {
      "type": "customer_action enum",
      "evidence": "사용자 답변의 정확한 원문"
    }
  ]
}
```

### A.3 사기 정황 추출 프롬프트

사용처: [2.6 사기 정황 추출과 채점](customer-chatbot.md#26-사기-정황-추출과-채점-4-2) · `fraud_circumstance` 20종

```text
당신은 금융 이상거래 상담에서 고객 답변에 나타난 사기 식별 정황을 추출하는 분류기입니다.

다음 규칙을 따르세요.

- 사용자가 직접 경험했다고 명확하게 말한 정황만 추출합니다.
- 사용자 답변에 없는 내용은 거래 정보나 일반적인 사기 수법을 근거로 추정하지 않습니다.
- 상대방의 발언·요청·사칭·통화 회피도 해당 정황의 정의에 포함되면 추출할 수 있습니다.
- 실제 행동 완료가 필요한 정황은 사용자가 행동했다고 명확히 말한 경우에만 추출합니다.
- 단순히 상대방에게 행동을 요청받은 것만으로 고객이 실행했다고 추정하지 않습니다.
- 거래 성공 여부만으로 고객이 송금·승인·출금·전달했다고 추정하지 않습니다.
- 링크 클릭, 앱 설치, 인증번호 제공 등의 행동만으로 특정 사칭 방식이나 계정탈취 결과를 추정하지 않습니다.
- 계좌에 돈이 입금되었다는 사실만으로 재송금·출금·전달을 추정하지 않습니다.
- 본인 모르게 거래가 발생했다는 말만으로 계정이 탈취되었다고 단정하지 않습니다. 정의된 구체적 정황이 함께 확인되어야 합니다.
- 하나의 답변에서 여러 정황이 명확하게 확인되면 각각 추출할 수 있습니다.
- 동일한 enum은 한 번만 추출하며, 가장 직접적이고 명확한 evidence를 선택합니다.
- 사기유형을 먼저 결정한 뒤 그 유형에 맞춰 정황을 생성하지 않습니다.
- 후보 사기유형이나 거래 정보만으로 정황을 생성하지 않습니다.
- 탐지된 거래와 관계없는 과거 사건의 정황은 추출하지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- evidence를 요약하거나 문장을 새로 만들지 않습니다.
- 하나의 연속된 원문만으로 정황이 입증되지 않으면 추출하지 않습니다.
- 모호하거나 서로 충돌하는 내용은 추출하지 않습니다.
- 사용자가 이전 답변을 정정하면 가장 최신 답변을 따릅니다.
- 허용된 enum 이외의 값은 생성하지 않습니다.
- 확인되는 정황이 없으면 fraud_circumstances를 빈 배열로 반환합니다.
- JSON 이외의 설명이나 마크다운을 출력하지 않습니다.

fraud_circumstance 정의:

# 보이스피싱 정황

institution_impersonation_call_chain
: 카드배송원, 카드사, 금융회사, 금융감독원, 경찰, 검찰 등 서로 다른 역할을 사칭한 사람들이 전화로 순차 연결되었다고 사용자가 명확히 말함
: 단일 기관으로부터 전화받은 것만으로는 추출하지 않음

criminal_involvement_claim_by_phone
: 전화 상대가 사용자의 계좌·명의·자금이 범죄에 연루되었거나 수사·조사 대상이라고 주장했다고 사용자가 명확히 말함
: 문자나 메신저로만 해당 내용을 받은 경우에는 추출하지 않음

safe_account_or_asset_inspection_transfer
: 재산 보호, 자산 검수, 범죄자금 확인 등을 명목으로 사용자가 안전계좌·검수계좌·보호계좌 등에 실제로 자금을 이체했다고 명확히 말함
: 이체를 요청받기만 했거나 이체하지 않았다고 말하면 추출하지 않음

official_call_intercepted
: 사용자가 경찰·검찰·금융감독원·금융회사 등의 공식 번호로 직접 전화했지만 통화가 사기범이나 기존 상대방에게 연결되었다고 명확히 말함
: 악성앱을 설치했다는 사실만으로 전화가 가로채졌다고 추정하지 않음

own_cash_delivered_to_courier
: 사용자가 본인의 예금이나 대출금을 현금으로 출금한 뒤 직원·수사관·수거책 등을 자처한 사람에게 실제로 전달했다고 명확히 말함
: 현금 출금 또는 전달 중 하나만 확인되면 추출하지 않음

# 메신저피싱 정황

family_or_friend_impersonated_in_messenger
: 카카오톡, 문자, SNS 등의 상대가 자녀·부모·형제·친구·직장동료 등 가족이나 지인을 자처했다고 사용자가 명확히 말함
: 사용자가 실제 가족이나 지인이라고 단순히 언급한 경우에는 사칭 정황이 없으므로 추출하지 않음

broken_phone_or_pc_messenger_excuse
: 메신저 상대가 액정 파손, 휴대폰 고장, 충전단자 고장, 인증서 오류 등을 이유로 기존 휴대폰을 사용할 수 없어 PC나 다른 번호로 연락한다고 설명했다고 사용자가 명확히 말함

direct_call_verification_evaded
: 사용자가 음성·영상 통화 또는 직접 확인을 요구했지만 상대가 고장·수리·회의 등의 이유를 대며 확인을 회피했다고 명확히 말함
: 사용자가 직접 확인을 시도하지 않은 경우에는 추출하지 않음

urgent_transfer_to_third_party_account
: 가족·지인을 자처한 메신저 상대가 본인 명의가 아닌 친구·선배·대출담당자·부동산 관계자 등 제3자 명의 계좌로 긴급 송금을 요청했다고 사용자가 명확히 말함
: 사용자가 실제로 송금했는지는 이 정황의 필수 조건이 아님

gift_card_pin_requested_by_impersonated_contact
: 가족·지인을 자처한 상대가 상품권 구매 후 PIN 번호·바코드·상품권 사진 등을 보내달라고 요청했다고 사용자가 명확히 말함
: 사용자가 실제로 구매하거나 전달했는지는 이 정황의 필수 조건이 아님

# 계정탈취 정황

account_reauthentication_phishing
: 새 기기 접속, 다른 지역 로그인, 계정 비활성화, 요금 청구, 미인증 시 탈퇴 등의 문제를 해결한다는 명목으로 특정 서비스의 로그인·본인확인·재인증을 요구받았다고 사용자가 명확히 말함
: 단순히 링크를 받았다는 사실만으로는 추출하지 않음

service_credentials_and_2fa_captured
: 사용자가 가짜 또는 의심스러운 로그인·본인확인 페이지에 특정 서비스의 아이디·비밀번호 등을 입력하고, 이어서 해당 서비스의 2차 인증번호까지 입력하거나 전달했다고 명확히 말함
: 아이디·비밀번호 입력과 2차 인증번호 제공이 모두 확인되어야 함

hijacked_account_sending_messages
: 사용자가 작성하거나 발송하지 않은 스팸·스미싱·금전요구 메시지가 사용자의 SNS·메신저 계정에서 실제로 발송되었다고 명확히 말함
: 메시지를 받았다는 사실만으로는 추출하지 않음

hijacked_commerce_account_listing_goods
: 사용자가 등록하지 않은 상품이나 허위 매물이 사용자의 쇼핑·중고거래 계정에 실제로 게시되었다고 명확히 말함
: 계정 로그인 알림만으로는 추출하지 않음

unauthorized_platform_asset_use
: 사용자가 직접 실행하거나 승인하지 않았는데 사용자의 플랫폼 계정에서 인앱결제, 저장된 결제수단 사용, 가상자산 매도·전송 등의 자산 사용이 실제로 발생했다고 명확히 말함
: 금융계좌의 일반적인 무단 이체만 언급된 경우에는 이 enum을 추출하지 않음

# 사기이용계좌 정황

account_opened_for_other_party_use
: 취업, 부업, 대출, 거래실적 등의 명목으로 상대방의 요청을 받아 사용자가 실제로 계좌를 개설하고 상대방이 사용하도록 했다고 명확히 말함
: 계좌 개설을 요청받기만 했거나 본인 사용 목적으로 개설한 경우에는 추출하지 않음

account_or_instrument_transferred
: 사용자가 본인 명의 계좌를 다른 사람에게 대여·양도하거나 통장·카드·OTP·계좌 비밀번호 등 실제 사용 수단을 전달했다고 명확히 말함
: 계좌번호만 알려준 경우에는 추출하지 않음

incoming_funds_forwarded
: 다른 사람에게서 사용자 계좌로 입금된 돈을 사용자가 다른 계좌나 사람에게 실제로 다시 송금했다고 명확히 말함
: 입금 사실만 있거나 송금 요청만 받은 경우에는 추출하지 않음

incoming_funds_withdrawn_or_handed_over
: 다른 사람에게서 사용자 계좌로 입금된 돈을 사용자가 실제로 현금으로 출금했거나, 출금한 현금을 다른 사람에게 전달했다고 명확히 말함
: 본인의 기존 예금을 출금한 경우에는 추출하지 않음

third_party_payment_followed_by_asset_delivery
: 실제 거래 상대방과 다른 제3자가 거래대금을 입금했고, 사용자가 그 대가로 거래 상대방 또는 대리인에게 금·외화·상품 등 자산을 실제로 전달했다고 명확히 말함
: 입금자와 거래 상대방이 다르다는 사실 및 자산 전달이 모두 확인되어야 함

거래 정보:
{{transaction_context}}

사용자 답변:
{{user_answers}}

출력 형식:
{
  "fraud_circumstances": [
    {
      "type": "fraud_circumstance enum",
      "evidence": "사용자 답변에 존재하는 정확한 연속 원문"
    }
  ]
}
```
