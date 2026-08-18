import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.config import CHAT_FALLBACK_EMAIL
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.repositories.agent_email import AgentEmailRepository


class FakeSession:
    def __init__(self, *, transaction, customer) -> None:
        self.transaction = transaction
        self.customer = customer

    def get(self, model, object_id):
        if model is Transaction:
            return self.transaction if object_id == 1 else None
        if model is Customer:
            return self.customer if object_id == 1 else None
        return None


class AgentEmailRepositoryTest(unittest.TestCase):
    def test_uses_trimmed_customer_email_when_available(self) -> None:
        repository = self._repository(email="  customer@example.com  ")

        context = repository.get_email_context(1)

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.recipient_email, "customer@example.com")

    def test_uses_fallback_email_when_customer_email_is_blank(self) -> None:
        for email in (None, "", "   "):
            with self.subTest(email=email):
                repository = self._repository(email=email)

                context = repository.get_email_context(1)

                self.assertIsNotNone(context)
                assert context is not None
                self.assertEqual(context.recipient_email, CHAT_FALLBACK_EMAIL)

    def test_returns_none_when_transaction_or_customer_is_missing(self) -> None:
        missing_transaction = AgentEmailRepository(
            FakeSession(transaction=None, customer=None)  # type: ignore[arg-type]
        )
        missing_customer = AgentEmailRepository(
            FakeSession(  # type: ignore[arg-type]
                transaction=self._transaction(),
                customer=None,
            )
        )

        self.assertIsNone(missing_transaction.get_email_context(1))
        self.assertIsNone(missing_customer.get_email_context(1))

    def _repository(self, *, email: str | None) -> AgentEmailRepository:
        customer = SimpleNamespace(
            id=1,
            name="홍길동",
            email=email,
        )
        return AgentEmailRepository(
            FakeSession(  # type: ignore[arg-type]
                transaction=self._transaction(),
                customer=customer,
            )
        )

    @staticmethod
    def _transaction():
        return SimpleNamespace(
            id=1,
            customer_id=1,
            transaction_datetime=datetime(2026, 8, 16, 9, 0, tzinfo=UTC),
            transaction_amount=100_000,
            channel="mobile",
        )


if __name__ == "__main__":
    unittest.main()
