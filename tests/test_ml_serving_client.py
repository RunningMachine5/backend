import unittest
from unittest.mock import Mock, patch

from app.services.ml_serving.client import MLServingClient


class MLServingClientAuthTest(unittest.TestCase):
    @staticmethod
    def _response(transaction_id: str) -> Mock:
        response = Mock()
        response.json.return_value = {
            "transaction_id": transaction_id,
            "is_fraud": False,
            "fraud_probability": 0.1,
            "shap": {"Transaction_Amount": 0.05},
            "model_name": "fdshield-rule-based-stub",
            "model_version": "0",
        }
        return response

    @patch("app.services.ml_serving.client.httpx.post")
    def test_none_mode_sends_no_authorization_header(self, post: Mock) -> None:
        post.return_value = self._response("TX_LOCAL")
        client = MLServingClient(base_url="http://localhost:8001", auth_mode="none")

        client.predict(transaction_id="TX_LOCAL", features={"amount": 1000})

        self.assertEqual(post.call_args.kwargs["headers"], {})

    @patch("app.services.ml_serving.client.httpx.post")
    def test_google_mode_sends_cached_provider_token(self, post: Mock) -> None:
        post.return_value = self._response("TX_CLOUD")
        token_provider = Mock(return_value="test-id-token")
        client = MLServingClient(
            base_url="https://ml-serving.example.run.app",
            auth_mode="google-id-token",
            token_provider=token_provider,
        )

        client.predict(transaction_id="TX_CLOUD", features={"amount": 1000})

        token_provider.assert_called_once_with()
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {"Authorization": "Bearer test-id-token"},
        )

    def test_unknown_auth_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ML_SERVING_AUTH_MODE"):
            MLServingClient(auth_mode="unknown")


if __name__ == "__main__":
    unittest.main()
