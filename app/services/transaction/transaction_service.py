from app.data.model import Transaction
from app.data.model.transaction import TransactionStatus
from app.dto.transaction import TransactionResponseDTO
from app.repositories.transaction import TransactionRepository


class TransactionService:
    def __init__(self, transaction_repository: TransactionRepository):
        self.transaction_repository = transaction_repository

    def save_transaction(self, tx, predict_result) -> tuple:
        # 예측 결과에 따라 거래 승인 여부, 에러 코드 추가
        if predict_result.predict_proba >= 0.5:
            # 거래 실패 상태 = True, error_code = f
            tx.transaction_status = TransactionStatus.DECLINED
            tx.error_code = "f"
            tx.balance = tx.initial_balance
            # Transaction 테이블에 적재
            tx_entity = Transaction(**tx.model_dump())
            stored_tx = self.transaction_repository.save_transaction(tx_entity)
            # transaction_id 다시 넣기
            predict_result.transaction_id = stored_tx.id

            return predict_result, TransactionResponseDTO(
                transaction_id = stored_tx.id,
                prediction_status = "DECLINED",
                predict_proba = predict_result.predict_proba,
                message = "이상거래 의심으로 거래가 거절되었습니다."
            )
        else:
            # Transaction 테이블에 적재
            tx_entity = Transaction(**tx.model_dump())
            stored_tx = self.transaction_repository.save_transaction(tx_entity)
            # transaction_id 다시 넣기
            predict_result.transaction_id = stored_tx.id

            return predict_result, TransactionResponseDTO(
                transaction_id = stored_tx.id,
                prediction_status = "COMPLETED",
                predict_proba = predict_result.predict_proba,
                message = "거래가 승인 되었습니다."
            )