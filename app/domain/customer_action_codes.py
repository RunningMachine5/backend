"""
고객이 수행한 19가지 행동 코드
https://miro.com/app/board/uXjVH3Y2H3Y=/?moveToWidget=3458764680653176675&cot=14
"""

from __future__ import annotations
from collections.abc import Mapping


DETECTED_TRANSACTION_INITIATED = "detected_transaction_initiated"
DETECTED_TRANSACTION_APPROVED = "detected_transaction_approved"
CASH_DELIVERED_AFTER_WITHDRAWAL = "cash_delivered_after_withdrawal"
RECEIVED_FUNDS_FORWARDED = "received_funds_forwarded"
RECEIVED_FUNDS_WITHDRAWN = "received_funds_withdrawn"
GOODS_OR_ASSET_DELIVERED_FOR_PAYMENT = "goods_or_asset_delivered_for_payment"
BANK_ACCOUNT_RENTED_OR_TRANSFERRED = "bank_account_rented_or_transferred"
ACCOUNT_ACCESS_OR_PAYMENT_INSTRUMENT_SHARED ="account_access_or_payment_instrument_shared"
PHISHING_LINK_OPENED = "phishing_link_opened"
FINANCIAL_CREDENTIALS_ENTERED_OR_SHARED = "financial_credentials_entered_or_shared"
OTP_OR_AUTHENTICATION_CODE_SHARED = "otp_or_authentication_code_shared"
IDENTITY_DOCUMENT_SHARED = "identity_document_shared"
CARD_INFORMATION_SHARED = "card_information_shared"
SUSPICIOUS_APP_INSTALLED = "suspicious_app_installed"
REMOTE_CONTROL_OR_SECURITY_PERMISSION_GRANTED ="remote_control_or_security_permission_granted"
LOAN_TAKEN_FOR_TRANSACTION = "loan_taken_for_transaction"
ACCOUNT_OPENED_FOR_OTHER_PARTY = "account_opened_for_other_party"
OPEN_BANKING_OR_EXTERNAL_FINANCE_LINKED = "open_banking_or_external_finance_linked"
CRYPTO_PURCHASED_OR_TRANSFERRED = "crypto_purchased_or_transferred"

# Mapping 은 딕셔너리류 타입을 표현하는 타입 힌트 중 하나, Mapping 타입 변수에는 변경 연산이 허용되지 않음 '읽기 전용'
CUSTOMER_ACTION_DESCRIPTIONS: Mapping[str, str] = {
    DETECTED_TRANSACTION_INITIATED: "고객이 탐지된 거래를 직접 입력하고 실행함",
    DETECTED_TRANSACTION_APPROVED: (
        "다른 사람이 준비한 탐지 거래를 고객이 인증하거나 승인함"
    ),
    CASH_DELIVERED_AFTER_WITHDRAWAL: (
        "현금을 출금한 뒤 다른 사람에게 직접 전달함"
    ),
    RECEIVED_FUNDS_FORWARDED: (
        "입금받은 돈을 다른 계좌나 사람에게 다시 송금함"
    ),
    RECEIVED_FUNDS_WITHDRAWN: "입금받은 돈을 현금으로 출금함",
    GOODS_OR_ASSET_DELIVERED_FOR_PAYMENT: (
        "입금의 대가로 물품·금·외화 등 자산을 전달함"
    ),
    BANK_ACCOUNT_RENTED_OR_TRANSFERRED: (
        "본인 명의 계좌를 다른 사람에게 대여하거나 양도함"
    ),
    ACCOUNT_ACCESS_OR_PAYMENT_INSTRUMENT_SHARED: (
        "금융계정 접근정보·통장·카드·OTP 기기 등을 다른 사람에게 전달함"
    ),
    PHISHING_LINK_OPENED: "상대방이 보낸 의심스러운 링크를 열거나 누름",
    FINANCIAL_CREDENTIALS_ENTERED_OR_SHARED: (
        "금융서비스 아이디·비밀번호·PIN을 입력하거나 전달함"
    ),
    OTP_OR_AUTHENTICATION_CODE_SHARED: (
        "OTP·문자·ARS 등의 인증번호를 입력하거나 전달함"
    ),
    IDENTITY_DOCUMENT_SHARED: "신분증 사진·사본·위임장 등을 전달함",
    CARD_INFORMATION_SHARED: (
        "카드번호·유효기간·CVC·카드 비밀번호를 전달함"
    ),
    SUSPICIOUS_APP_INSTALLED: "상대방이 안내한 앱이나 APK를 설치함",
    REMOTE_CONTROL_OR_SECURITY_PERMISSION_GRANTED: (
        "원격제어·접근성·기기관리자 등의 권한을 허용함"
    ),
    LOAN_TAKEN_FOR_TRANSACTION: "탐지 거래의 자금을 마련하기 위해 대출을 실행함",
    ACCOUNT_OPENED_FOR_OTHER_PARTY: (
        "상대방 요청에 따라 계좌를 개설하거나 사용하게 함"
    ),
    OPEN_BANKING_OR_EXTERNAL_FINANCE_LINKED: (
        "상대방 요청에 따라 오픈뱅킹이나 외부 금융서비스를 연결함"
    ),
    CRYPTO_PURCHASED_OR_TRANSFERRED: (
        "탐지 거래와 관련해 가상자산을 구매하거나 외부 지갑으로 전송함"
    ),
}


# 한국어 가이드 문서와의 임베딩 유사도를 높이기 위해 영어로 된 코드 대신 이 쿼리를 사용한다
CUSTOMER_ACTION_SEARCH_QUERIES: Mapping[str, str] = {
    DETECTED_TRANSACTION_INITIATED: (
        "의심 거래를 직접 입력하고 실행했을 때 대응 방법"
    ),
    DETECTED_TRANSACTION_APPROVED: (
        "다른 사람이 준비한 의심 거래를 인증하거나 승인했을 때 대응 방법"
    ),
    CASH_DELIVERED_AFTER_WITHDRAWAL: (
        "현금을 출금해 다른 사람에게 직접 전달했을 때 대응 방법"
    ),
    RECEIVED_FUNDS_FORWARDED: (
        "입금받은 돈을 다른 계좌나 사람에게 다시 송금했을 때 대응 방법"
    ),
    RECEIVED_FUNDS_WITHDRAWN: (
        "입금받은 돈을 현금으로 출금했을 때 대응 방법"
    ),
    GOODS_OR_ASSET_DELIVERED_FOR_PAYMENT: (
        "입금 대가로 물품·금·외화 등 자산을 전달했을 때 대응 방법"
    ),
    BANK_ACCOUNT_RENTED_OR_TRANSFERRED: (
        "본인 명의 계좌를 다른 사람에게 대여하거나 양도했을 때 대응 방법"
    ),
    ACCOUNT_ACCESS_OR_PAYMENT_INSTRUMENT_SHARED: (
        "금융계정 접근정보·통장·카드·OTP 기기를 전달했을 때 대응 방법"
    ),
    PHISHING_LINK_OPENED: (
        "상대방이 보낸 의심스러운 링크를 열거나 눌렀을 때 대응 방법"
    ),
    FINANCIAL_CREDENTIALS_ENTERED_OR_SHARED: (
        "금융서비스 아이디·비밀번호·PIN을 입력하거나 전달했을 때 대응 방법"
    ),
    OTP_OR_AUTHENTICATION_CODE_SHARED: (
        "OTP·문자·ARS 인증번호를 입력하거나 전달했을 때 대응 방법"
    ),
    IDENTITY_DOCUMENT_SHARED: (
        "신분증 사진·사본·위임장을 전달했을 때 대응 방법"
    ),
    CARD_INFORMATION_SHARED: (
        "카드번호·유효기간·CVC·카드 비밀번호를 전달했을 때 대응 방법"
    ),
    SUSPICIOUS_APP_INSTALLED: (
        "상대방이 안내한 앱이나 APK를 설치했을 때 대응 방법"
    ),
    REMOTE_CONTROL_OR_SECURITY_PERMISSION_GRANTED: (
        "원격제어·접근성·기기관리자 권한을 허용했을 때 대응 방법"
    ),
    LOAN_TAKEN_FOR_TRANSACTION: (
        "의심 거래 자금을 마련하려고 대출을 실행했을 때 대응 방법"
    ),
    ACCOUNT_OPENED_FOR_OTHER_PARTY: (
        "상대방 요청으로 계좌를 개설하거나 사용하게 했을 때 대응 방법"
    ),
    OPEN_BANKING_OR_EXTERNAL_FINANCE_LINKED: (
        "상대방 요청으로 오픈뱅킹이나 외부 금융서비스를 연결했을 때 대응 방법"
    ),
    CRYPTO_PURCHASED_OR_TRANSFERRED: (
        "의심 거래와 관련해 가상자산을 구매하거나 외부 지갑으로 전송했을 때 대응 방법"
    ),
}

FINAL_CUSTOMER_ACTION_CODES = frozenset(CUSTOMER_ACTION_DESCRIPTIONS)
