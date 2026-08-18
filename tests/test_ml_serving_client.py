import unittest
from unittest.mock import Mock, patch

import httpx

from app.services.ml_serving.client import MLServingClient, MLServingError


class MLServingClientAuthTest(unittest.TestCase):
    @staticmethod
    def _response() -> Mock:
        response = Mock()
        response.json.return_value = {
            "predict_result": 0,
            "predict_proba": 0.1,
            "shap_values": {"transaction_amount": 0.05},
            "model_name": "fdshield-fraud-detector-v2",
            "model_version": "1",
        }
        return response

    @patch("app.services.ml_serving.client.httpx.post")
    def test_none_mode_sends_no_authorization_header(self, post: Mock) -> None:
        post.return_value = self._response()
        client = MLServingClient(base_url="http://localhost:8001", auth_mode="none")

        client.predict(transaction_id=1, features={"amount": 1000})

        self.assertEqual(post.call_args.kwargs["headers"], {})
        self.assertEqual(
            post.call_args.args[0],
            "http://localhost:8001/ml/predict",
        )
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"amount": 1000},
        )

    @patch("app.services.ml_serving.client.httpx.post")
    def test_google_mode_sends_cached_provider_token(self, post: Mock) -> None:
        post.return_value = self._response()
        token_provider = Mock(return_value="test-id-token")
        client = MLServingClient(
            base_url="https://ml-serving.example.run.app",
            auth_mode="google-id-token",
            token_provider=token_provider,
        )

        client.predict(transaction_id=2, features={"amount": 1000})

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
            self._response(),
        ]
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        prediction = client.predict(
            transaction_id=3,
            features={"amount": 1000},
        )

        self.assertEqual(prediction.transaction_id, 3)
        self.assertEqual(post.call_count, 2)

    @patch("app.services.ml_serving.client.httpx.post")
    def test_retryable_server_error_is_retried(self, post: Mock) -> None:
        failed = httpx.Response(
            503,
            request=httpx.Request("POST", "http://localhost:8001/ml/predict"),
        )
        post.side_effect = [failed, self._response()]
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        client.predict(transaction_id=4, features={"amount": 1000})

        self.assertEqual(post.call_count, 2)

    @patch("app.services.ml_serving.client.httpx.post")
    def test_bad_request_is_not_retried(self, post: Mock) -> None:
        post.return_value = httpx.Response(
            400,
            request=httpx.Request("POST", "http://localhost:8001/ml/predict"),
        )
        client = MLServingClient(
            base_url="http://localhost:8001",
            auth_mode="none",
            max_attempts=2,
            retry_delay_seconds=0,
        )

        with self.assertRaises(MLServingError):
            client.predict(transaction_id=5, features={"amount": 1000})

        post.assert_called_once()

    def test_injected_http_client_reuses_connection_and_closes(self) -> None:
        http_client = Mock()
        http_client.post.side_effect = [self._response(), self._response()]
        client = MLServingClient(
            base_url="https://ml-serving.example.run.app",
            auth_mode="none",
            http_client=http_client,
        )

        client.predict(transaction_id=6, features={"amount": 1000})
        client.predict(transaction_id=7, features={"amount": 2000})
        client.close()

        self.assertEqual(http_client.post.call_count, 2)
        http_client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
