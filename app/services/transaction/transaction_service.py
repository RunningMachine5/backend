from app.data.model import Transaction
from app.data.model.transaction import TransactionStatus
from app.repositories.transaction import TransactionRepository


class TransactionService:
    def __init__(self, transaction_repository: TransactionRepository):
        self.transaction_repository = transaction_repository

    def save_transaction(self, tx, predict_result) -> tuple[Transaction, bool]:
        """Backend의 0.5 기준으로 거래 상태를 결정하고 저장한다."""

        # 예측 결과에 따라 거래 승인 여부, 에러 코드 추가
        is_fraud = predict_result.predict_proba >= 0.5
        if is_fraud:
            # 이상거래는 거절하고 출금 전 잔액을 유지한다.
            tx.transaction_status = TransactionStatus.DECLINED
            tx.error_code = "FRAUD"
            tx.balance = tx.initial_balance
        else:
            tx.transaction_status = TransactionStatus.APPROVED
            tx.error_code = None

        # Transaction 테이블에 적재
        tx_entity = Transaction(**tx.model_dump())
        stored_tx = self.transaction_repository.save_transaction(tx_entity)

        # 정상 거래만 실제 출금 계좌 잔액에 반영한다.
        if not is_fraud and stored_tx.balance is not None:
            self.transaction_repository.update_source_balance(
                stored_tx.source_account_number,
                stored_tx.balance,
            )

        return stored_tx, is_fraud
