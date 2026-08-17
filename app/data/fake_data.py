from app.dto.transaction import TransactionDTO


# 첫 거래는 사용자가 제공한 예시이며 VERY_HIGH 이메일 분기를 보여준다.
# 둘째 거래는 그 외 위험등급의 RAG/대시보드 분기를 보여준다.
FAKE_TRANSACTIONS = [
    TransactionDTO(
        user_id="USR_100123",
        transaction_time="2026-07-30T12:47:22+09:00",
        amount=85_000_000, # 해당 거래 금액 8,500만원 - 평균 대비 460배
        user_amount_std_dev=185_000.42, # 평균 금액 18.5만원
        payment_method="CARD",
        merchant_category="VEHICLES",
        user_name="홍길동",
        email="sample@email.com",
    ),
    TransactionDTO(
        user_id="USR_100456",
        transaction_time="2026-07-30T13:10:00+09:00",
        amount=12_000_000, # 해당 거래 금액 1,200만원 - 평균 대비 40배
        user_amount_std_dev=300_000.00, # 평균 금액 30만원
        payment_method="CARD",
        merchant_category="ELECTRONICS",
        user_name="김철수",
        email="kimcheolsu@email.com",
    ),
]


# Fake VectorDB는 어떤 질의에도 아래의 동일한 문맥을 반환한다.
MONITORING_GUIDE_TEXT = (
    "거래 당사자에게 본인 거래 여부를 확인하고, 결제수단의 추가 사용을 점검한다. "
    "미확인 거래이면 내부 FDS 절차에 따라 거래 검토와 계정 보호 조치를 안내한다."
)

