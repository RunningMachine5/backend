"""
고객 답변에서 유추할 수 있는 사기 정황
https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680653176676&cot=14
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)

INSTITUTION_IMPERSONATION_CALL_CHAIN = "institution_impersonation_call_chain"
CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE = "criminal_involvement_claim_by_phone"
SAFE_ACCOUNT_OR_ASSET_INSPECTION_TRANSFER ="safe_account_or_asset_inspection_transfer"
OFFICIAL_CALL_INTERCEPTED = "official_call_intercepted"
OWN_CASH_DELIVERED_TO_COURIER = "own_cash_delivered_to_courier"
FAMILY_OR_FRIEND_IMPERSONATED_IN_MESSENGER ="family_or_friend_impersonated_in_messenger"
BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE = "broken_phone_or_pc_messenger_excuse"
DIRECT_CALL_VERIFICATION_EVADED = "direct_call_verification_evaded"
URGENT_TRANSFER_TO_THIRD_PARTY_ACCOUNT = "urgent_transfer_to_third_party_account"
GIFT_CARD_PIN_REQUESTED_BY_IMPERSONATED_CONTACT = "gift_card_pin_requested_by_impersonated_contact"
ACCOUNT_REAUTHENTICATION_PHISHING = "account_reauthentication_phishing"
SERVICE_CREDENTIALS_AND_2FA_CAPTURED = "service_credentials_and_2fa_captured"
HIJACKED_ACCOUNT_SENDING_MESSAGES = "hijacked_account_sending_messages"
HIJACKED_COMMERCE_ACCOUNT_LISTING_GOODS =  "hijacked_commerce_account_listing_goods"
UNAUTHORIZED_PLATFORM_ASSET_USE = "unauthorized_platform_asset_use"
ACCOUNT_OPENED_FOR_OTHER_PARTY_USE = "account_opened_for_other_party_use"
ACCOUNT_OR_INSTRUMENT_TRANSFERRED = "account_or_instrument_transferred"
INCOMING_FUNDS_FORWARDED = "incoming_funds_forwarded"
INCOMING_FUNDS_WITHDRAWN_OR_HANDED_OVER =  "incoming_funds_withdrawn_or_handed_over"
THIRD_PARTY_PAYMENT_FOLLOWED_BY_ASSET_DELIVERY = "third_party_payment_followed_by_asset_delivery"

# LLM에 제공할 사기 정황 판별 조건
FRAUD_CIRCUMSTANCE_DESCRIPTIONS: Mapping[str, str] = {
    INSTITUTION_IMPERSONATION_CALL_CHAIN: (
        "카드배송원, 카드사, 금융회사, 금융감독원, 경찰, 검찰 등 서로 다른 역할을 "
        "사칭한 사람들이 전화로 순차 연결되었다고 사용자가 명확히 말함\n"
        "단일 기관으로부터 전화받은 것만으로는 추출하지 않음"
    ),
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE: (
        "전화 상대가 사용자의 계좌·명의·자금이 범죄에 연루되었거나 수사·조사 "
        "대상이라고 주장했다고 사용자가 명확히 말함\n"
        "문자나 메신저로만 해당 내용을 받은 경우에는 추출하지 않음"
    ),
    SAFE_ACCOUNT_OR_ASSET_INSPECTION_TRANSFER: (
        "재산 보호, 자산 검수, 범죄자금 확인 등을 명목으로 사용자가 안전계좌·"
        "검수계좌·보호계좌 등에 실제로 자금을 이체했다고 명확히 말함\n"
        "이체를 요청받기만 했거나 이체하지 않았다고 말하면 추출하지 않음"
    ),
    OFFICIAL_CALL_INTERCEPTED: (
        "사용자가 경찰·검찰·금융감독원·금융회사 등의 공식 번호로 직접 전화했지만 "
        "통화가 사기범이나 기존 상대방에게 연결되었다고 명확히 말함\n"
        "악성앱을 설치했다는 사실만으로 전화가 가로채졌다고 추정하지 않음"
    ),
    OWN_CASH_DELIVERED_TO_COURIER: (
        "사용자가 본인의 예금이나 대출금을 현금으로 출금한 뒤 직원·수사관·수거책 "
        "등을 자처한 사람에게 실제로 전달했다고 명확히 말함\n"
        "현금 출금 또는 전달 중 하나만 확인되면 추출하지 않음"
    ),
    FAMILY_OR_FRIEND_IMPERSONATED_IN_MESSENGER: (
        "카카오톡, 문자, SNS 등의 상대가 자녀·부모·형제·친구·직장동료 등 가족이나 "
        "지인을 자처했다고 사용자가 명확히 말함\n"
        "사용자가 실제 가족이나 지인이라고 단순히 언급한 경우에는 사칭 정황이 "
        "없으므로 추출하지 않음"
    ),
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE: (
        "메신저 상대가 액정 파손, 휴대폰 고장, 충전단자 고장, 인증서 오류 등을 "
        "이유로 기존 휴대폰을 사용할 수 없어 PC나 다른 번호로 연락한다고 "
        "설명했다고 사용자가 명확히 말함"
    ),
    DIRECT_CALL_VERIFICATION_EVADED: (
        "사용자가 음성·영상 통화 또는 직접 확인을 요구했지만 상대가 고장·수리·회의 "
        "등의 이유를 대며 확인을 회피했다고 명확히 말함\n"
        "사용자가 직접 확인을 시도하지 않은 경우에는 추출하지 않음"
    ),
    URGENT_TRANSFER_TO_THIRD_PARTY_ACCOUNT: (
        "가족·지인을 자처한 메신저 상대가 본인 명의가 아닌 친구·선배·대출담당자·"
        "부동산 관계자 등 제3자 명의 계좌로 긴급 송금을 요청했다고 사용자가 "
        "명확히 말함\n"
        "사용자가 실제로 송금했는지는 이 정황의 필수 조건이 아님"
    ),
    GIFT_CARD_PIN_REQUESTED_BY_IMPERSONATED_CONTACT: (
        "가족·지인을 자처한 상대가 상품권 구매 후 PIN 번호·바코드·상품권 사진 등을 "
        "보내달라고 요청했다고 사용자가 명확히 말함\n"
        "사용자가 실제로 구매하거나 전달했는지는 이 정황의 필수 조건이 아님"
    ),
    ACCOUNT_REAUTHENTICATION_PHISHING: (
        "새 기기 접속, 다른 지역 로그인, 계정 비활성화, 요금 청구, 미인증 시 탈퇴 "
        "등의 문제를 해결한다는 명목으로 특정 서비스의 로그인·본인확인·재인증을 "
        "요구받았다고 사용자가 명확히 말함\n"
        "단순히 링크를 받았다는 사실만으로는 추출하지 않음"
    ),
    SERVICE_CREDENTIALS_AND_2FA_CAPTURED: (
        "사용자가 가짜 또는 의심스러운 로그인·본인확인 페이지에 특정 서비스의 "
        "아이디·비밀번호 등을 입력하고, 이어서 해당 서비스의 2차 인증번호까지 "
        "입력하거나 전달했다고 명확히 말함\n"
        "아이디·비밀번호 입력과 2차 인증번호 제공이 모두 확인되어야 함"
    ),
    HIJACKED_ACCOUNT_SENDING_MESSAGES: (
        "사용자가 작성하거나 발송하지 않은 스팸·스미싱·금전요구 메시지가 사용자의 "
        "SNS·메신저 계정에서 실제로 발송되었다고 명확히 말함\n"
        "메시지를 받았다는 사실만으로는 추출하지 않음"
    ),
    HIJACKED_COMMERCE_ACCOUNT_LISTING_GOODS: (
        "사용자가 등록하지 않은 상품이나 허위 매물이 사용자의 쇼핑·중고거래 계정에 "
        "실제로 게시되었다고 명확히 말함\n"
        "계정 로그인 알림만으로는 추출하지 않음"
    ),
    UNAUTHORIZED_PLATFORM_ASSET_USE: (
        "사용자가 직접 실행하거나 승인하지 않았는데 사용자의 플랫폼 계정에서 "
        "인앱결제, 저장된 결제수단 사용, 가상자산·게임 아이템·포인트 매도나 전송 "
        "등의 자산 사용이 실제로 발생했다고 명확히 말함\n"
        "금융계좌의 일반적인 무단 이체만 언급된 경우에는 이 enum을 추출하지 않음"
    ),
    ACCOUNT_OPENED_FOR_OTHER_PARTY_USE: (
        "취업, 부업, 대출, 거래실적 등의 명목으로 상대방의 요청을 받아 사용자가 "
        "실제로 계좌를 개설하고 상대방이 사용하도록 했다고 명확히 말함\n"
        "계좌 개설을 요청받기만 했거나 본인 사용 목적으로 개설한 경우에는 추출하지 않음"
    ),
    ACCOUNT_OR_INSTRUMENT_TRANSFERRED: (
        "사용자가 본인 명의 계좌를 다른 사람에게 대여·양도하거나 통장·카드·OTP·"
        "계좌 비밀번호 등 실제 사용 수단을 전달했다고 명확히 말함\n"
        "계좌번호만 알려준 경우에는 추출하지 않음"
    ),
    INCOMING_FUNDS_FORWARDED: (
        "다른 사람에게서 사용자 계좌로 입금된 돈을 사용자가 다른 계좌나 사람에게 "
        "실제로 다시 송금했다고 명확히 말함\n"
        "입금 사실만 있거나 송금 요청만 받은 경우에는 추출하지 않음"
    ),
    INCOMING_FUNDS_WITHDRAWN_OR_HANDED_OVER: (
        "다른 사람에게서 사용자 계좌로 입금된 돈을 사용자가 실제로 현금으로 "
        "출금했거나, 출금한 현금을 다른 사람에게 전달했다고 명확히 말함\n"
        "본인의 기존 예금을 출금한 경우에는 추출하지 않음"
    ),
    THIRD_PARTY_PAYMENT_FOLLOWED_BY_ASSET_DELIVERY: (
        "실제 거래 상대방과 다른 제3자가 거래대금을 입금했고, 사용자가 그 대가로 "
        "거래 상대방 또는 대리인에게 금·외화·상품 등 자산을 실제로 전달했다고 "
        "명확히 말함\n"
        "입금자와 거래 상대방이 다르다는 사실 및 자산 전달이 모두 확인되어야 함"
    ),
}

"""
고객이 말한 사기 정황을 통해 사기유형별 점수를 매긴다 채점표는
https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680653060843&cot=14
여기에 정의되어있다
"""
def _scores(
    voice_phishing: int = 0,
    messenger_phishing: int = 0,
    account_takeover: int = 0,
    fraud_used_account: int = 0,
) -> Mapping[str, int]:
    return {
        VOICE_PHISHING: voice_phishing,
        MESSENGER_PHISHING: messenger_phishing,
        ACCOUNT_TAKEOVER: account_takeover,
        FRAUD_USED_ACCOUNT: fraud_used_account,
    }


# scoring.md의 20종 × 4유형 채점표와 항상 함께 변경한다.
FRAUD_CIRCUMSTANCE_SCORES: Mapping[str, Mapping[str, int]] = {
    INSTITUTION_IMPERSONATION_CALL_CHAIN: _scores(voice_phishing=4),
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE: _scores(voice_phishing=3),
    SAFE_ACCOUNT_OR_ASSET_INSPECTION_TRANSFER: _scores(voice_phishing=4),
    OFFICIAL_CALL_INTERCEPTED: _scores(voice_phishing=4, account_takeover=1),
    OWN_CASH_DELIVERED_TO_COURIER: _scores(voice_phishing=4),
    FAMILY_OR_FRIEND_IMPERSONATED_IN_MESSENGER: _scores(
        messenger_phishing=4
    ),
    BROKEN_PHONE_OR_PC_MESSENGER_EXCUSE: _scores(messenger_phishing=3),
    DIRECT_CALL_VERIFICATION_EVADED: _scores(messenger_phishing=3),
    URGENT_TRANSFER_TO_THIRD_PARTY_ACCOUNT: _scores(messenger_phishing=4),
    GIFT_CARD_PIN_REQUESTED_BY_IMPERSONATED_CONTACT: _scores(
        messenger_phishing=4
    ),
    ACCOUNT_REAUTHENTICATION_PHISHING: _scores(account_takeover=3),
    SERVICE_CREDENTIALS_AND_2FA_CAPTURED: _scores(account_takeover=4),
    HIJACKED_ACCOUNT_SENDING_MESSAGES: _scores(
        messenger_phishing=2,
        account_takeover=4,
    ),
    HIJACKED_COMMERCE_ACCOUNT_LISTING_GOODS: _scores(account_takeover=4),
    UNAUTHORIZED_PLATFORM_ASSET_USE: _scores(account_takeover=4),
    ACCOUNT_OPENED_FOR_OTHER_PARTY_USE: _scores(fraud_used_account=4),
    ACCOUNT_OR_INSTRUMENT_TRANSFERRED: _scores(fraud_used_account=4),
    INCOMING_FUNDS_FORWARDED: _scores(fraud_used_account=4),
    INCOMING_FUNDS_WITHDRAWN_OR_HANDED_OVER: _scores(fraud_used_account=4),
    THIRD_PARTY_PAYMENT_FOLLOWED_BY_ASSET_DELIVERY: _scores(
        voice_phishing=1,
        fraud_used_account=4,
    ),
}

FINAL_FRAUD_CIRCUMSTANCE_CODES = frozenset(FRAUD_CIRCUMSTANCE_DESCRIPTIONS)
