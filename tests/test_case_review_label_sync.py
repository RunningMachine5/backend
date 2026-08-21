import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api.case_query import save_case_review
from app.data.model.agent import AgentCase, AgentReview
from app.data.model.transaction_label import TransactionLabel
from app.dto.case_review import CaseReviewUpsertRequest, ReviewDecision


class CaseReviewLabelSyncTest(unittest.TestCase):
    def make_session(
        self,
        label: TransactionLabel | None = None,
    ) -> MagicMock:
        session = MagicMock()

        def get(model, _key):
            if model is AgentCase:
                return SimpleNamespace(transaction_id=42)
            if model is AgentReview:
                return None
            if model is TransactionLabel:
                return label
            return None

        session.get.side_effect = get
        return session

    @staticmethod
    def saved_objects(session: MagicMock) -> list[object]:
        return [call.args[0] for call in session.add.call_args_list]

    def test_confirmed_fraud_creates_training_label(self) -> None:
        session = self.make_session()

        save_case_review(
            case_id="CASE-1",
            request=CaseReviewUpsertRequest(
                decision=ReviewDecision.CONFIRMED_FRAUD,
                confirmed_fraud_type="ACCOUNT_TAKEOVER",
            ),
            session=session,
        )

        label = next(
            item
            for item in self.saved_objects(session)
            if isinstance(item, TransactionLabel)
        )
        self.assertIs(label.confirmed_is_fraud, True)
        session.commit.assert_called_once_with()

    def test_false_positive_updates_existing_training_label(self) -> None:
        existing_label = TransactionLabel(
            transaction_id=42,
            confirmed_is_fraud=True,
        )
        session = self.make_session(existing_label)

        save_case_review(
            case_id="CASE-1",
            request=CaseReviewUpsertRequest(
                decision=ReviewDecision.FALSE_POSITIVE,
            ),
            session=session,
        )

        self.assertIs(existing_label.confirmed_is_fraud, False)
        session.commit.assert_called_once_with()

    def test_on_hold_keeps_existing_training_label(self) -> None:
        existing_label = TransactionLabel(
            transaction_id=42,
            confirmed_is_fraud=True,
        )
        session = self.make_session(existing_label)

        save_case_review(
            case_id="CASE-1",
            request=CaseReviewUpsertRequest(
                decision=ReviewDecision.ON_HOLD,
            ),
            session=session,
        )

        self.assertIs(existing_label.confirmed_is_fraud, True)
        self.assertFalse(any(
            isinstance(item, TransactionLabel)
            for item in self.saved_objects(session)
        ))
        session.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
