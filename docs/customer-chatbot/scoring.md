# 고객 대응 챗봇 — 사기 정황 내부 채점표

[고객 대응 챗봇 설계 (PRD)](README.md)의 부속 문서다.
추출된 `fraud_circumstance`를 사기유형별 점수로 환산하는 표를 담는다.

- 언제 추출하고 언제 집계하는지: PRD [2.6 사기 정황 추출과 채점](README.md#26-사기-정황-추출과-채점-4-2)
- 추출 프롬프트와 20종 정의: [LLM 프롬프트 A.3](prompts.md#a3-사기-정황-추출-프롬프트)
- 점수를 저장하는 테이블: [DB·스키마 3.7](schema.md#37-fraud_type_score_after_chat--구조-변경)

이 표는 코드에서 `app/domain/fraud_circumstance_codes.py`의
`FRAUD_CIRCUMSTANCE_SCORES: Mapping[str, Mapping[str, int]]`로 관리한다
([DB·스키마 3.8](schema.md#38-appdomain-enum-코드-상수화)).
**표와 코드 상수는 한 소스여야 하므로, 값을 바꿀 때 양쪽을 함께 고친다.**

정황 하나가 여러 유형에 점수를 주므로 이 매핑 안에 정황 → 사기유형 관계가 포함된다.
별도의 정황-유형 매핑을 따로 두지 않는다.

---

## 채점표

정황 하나가 여러 유형에 점수를 줄 수 있다. 점수는 유형별로 누적한다.

| 정황 enum | 보이스피싱 | 메신저피싱 | 계정탈취 | 사기이용계좌 |
| --- | --- | --- | --- | --- |
| `institution_impersonation_call_chain` | 4 | 0 | 0 | 0 |
| `criminal_involvement_claim_by_phone` | 3 | 0 | 0 | 0 |
| `safe_account_or_asset_inspection_transfer` | 4 | 0 | 0 | 0 |
| `official_call_intercepted` | 4 | 0 | 1 | 0 |
| `own_cash_delivered_to_courier` | 4 | 0 | 0 | 0 |
| `family_or_friend_impersonated_in_messenger` | 0 | 4 | 0 | 0 |
| `broken_phone_or_pc_messenger_excuse` | 0 | 3 | 0 | 0 |
| `direct_call_verification_evaded` | 0 | 3 | 0 | 0 |
| `urgent_transfer_to_third_party_account` | 0 | 4 | 0 | 0 |
| `gift_card_pin_requested_by_impersonated_contact` | 0 | 4 | 0 | 0 |
| `account_reauthentication_phishing` | 0 | 0 | 3 | 0 |
| `service_credentials_and_2fa_captured` | 0 | 0 | 4 | 0 |
| `hijacked_account_sending_messages` | 0 | 2 | 4 | 0 |
| `hijacked_commerce_account_listing_goods` | 0 | 0 | 4 | 0 |
| `unauthorized_platform_asset_use` | 0 | 0 | 4 | 0 |
| `account_opened_for_other_party_use` | 0 | 0 | 0 | 4 |
| `account_or_instrument_transferred` | 0 | 0 | 0 | 4 |
| `incoming_funds_forwarded` | 0 | 0 | 0 | 4 |
| `incoming_funds_withdrawn_or_handed_over` | 0 | 0 | 0 | 4 |
| `third_party_payment_followed_by_asset_delivery` | 1 | 0 | 0 | 4 |
