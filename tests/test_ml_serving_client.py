import unittest
from unittest.mock import Mock, patch

import httpx

from app.services.ml_serving.client import MLServingClient, MLServingError


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

    @patch("app.services.ml_serving.client.httpx.post")
    def test_transient_timeout_is_retried_once(self, post: Mock) -> None:
        post.side_effect = [
            httpx.ReadTimeout("cold start timeout"),
            self._response("TX_RETRY"),
        ]
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        prediction = client.predict(
            transaction_id="TX_RETRY",
            features={"amount": 1000},
        )

        self.assertEqual(prediction.transaction_id, "TX_RETRY")
        self.assertEqual(post.call_count, 2)

    @patch("app.services.ml_serving.client.httpx.post")
    def test_retryable_server_error_is_retried(self, post: Mock) -> None:
        failed = httpx.Response(
            503,
            request=httpx.Request("POST", "http://localhost:8001/predict"),
        )
        post.side_effect = [failed, self._response("TX_503")]
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        client.predict(transaction_id="TX_503", features={"amount": 1000})

        self.assertEqual(post.call_count, 2)

    @patch("app.services.ml_serving.client.httpx.post")
    def test_bad_request_is_not_retried(self, post: Mock) -> None:
        post.return_value = httpx.Response(
            400,
            request=httpx.Request("POST", "http://localhost:8001/predict"),
        )
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        with self.assertRaises(MLServingError):
            client.predict(transaction_id="TX_400", features={"amount": 1000})

        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
