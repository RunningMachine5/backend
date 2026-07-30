from app.data.fake_data import FAKE_TRANSACTIONS
from app.dto.transaction import TransactionDTO


class FakeTransactionRepository:
    """실제 RDB 대신 하드코딩 목록에서 사용자 거래를 조회한다."""

    def find_latest_by_user_id(self, user_id: str) -> TransactionDTO:
        """사용자 식별자가 같은 목록의 첫 거래를 관련 거래로 반환한다."""
        return [
            transaction
            for transaction in FAKE_TRANSACTIONS
            if transaction.user_id == user_id
        ][0]

