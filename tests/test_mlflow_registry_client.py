import unittest
from unittest.mock import Mock, patch

from app.services.mlops.mlflow import MLflowRegistryClient, MLflowRegistryError


def response(payload: dict) -> Mock:
    result = Mock()
    result.json.return_value = payload
    return result


class MLflowRegistryClientTest(unittest.TestCase):
    def make_client(self) -> MLflowRegistryClient:
        return MLflowRegistryClient(
            tracking_uri="https://mlflow.example",
            username="user",
            password="secret",
            timeout_seconds=5,
        )

    @patch("app.services.mlops.mlflow.httpx.request")
    def test_check_registry_reads_authenticated_registry_endpoint(
        self, request: Mock
    ) -> None:
        request.return_value = response({"registered_models": []})

        self.make_client().check_registry()

        call = request.call_args
        self.assertTrue(call.args[1].endswith("/registered-models/search"))
        self.assertEqual(call.kwargs["params"], {"max_results": 1})
        self.assertEqual(call.kwargs["auth"], ("user", "secret"))

    @patch("app.services.mlops.mlflow.httpx.request")
    def test_resolve_model_version_matches_registered_model_and_run(
        self, request: Mock
    ) -> None:
        request.return_value = response(
            {
                "model_versions": [
                    {"name": "fraud-model", "version": "16", "run_id": "old"},
                    {
                        "name": "fraud-model",
                        "version": "17",
                        "run_id": "candidate-run",
                    },
                ]
            }
        )

        version = self.make_client().resolve_model_version(
            "fraud-model", "candidate-run"
        )

        self.assertEqual(version, "17")
        call = request.call_args
        self.assertTrue(call.args[1].endswith("/model-versions/search"))
        self.assertEqual(call.kwargs["auth"], ("user", "secret"))

    @patch("app.services.mlops.mlflow.httpx.request")
    def test_resolve_model_version_rejects_missing_or_ambiguous_match(
        self, request: Mock
    ) -> None:
        request.return_value = response({"model_versions": []})
        with self.assertRaisesRegex(MLflowRegistryError, "찾지 못했습니다"):
            self.make_client().resolve_model_version("fraud-model", "missing")

        request.return_value = response(
            {
                "model_versions": [
                    {"name": "fraud-model", "version": "17", "run_id": "same"},
                    {"name": "fraud-model", "version": "18", "run_id": "same"},
                ]
            }
        )
        with self.assertRaisesRegex(MLflowRegistryError, "하나로"):
            self.make_client().resolve_model_version("fraud-model", "same")

    @patch("app.services.mlops.mlflow.httpx.request")
    def test_get_model_details_normalizes_mlflow_run_data(self, request: Mock) -> None:
        request.side_effect = [
            response(
                {
                    "run": {
                        "info": {
                            "run_id": "candidate-run",
                            "artifact_uri": (
                                "mlflow-artifacts:/1/candidate-run/artifacts"
                            ),
                        },
                        "data": {
                            "metrics": [
                                {"key": "validation_pr_auc", "value": 0.95}
                            ],
                            "params": [
                                {"key": "decision_threshold", "value": "0.61"}
                            ],
                            "tags": [
                                {
                                    "key": "promotion_recommendation",
                                    "value": "RECOMMENDED",
                                },
                            ],
                        },
                    }
                }
            ),
            response(
                {
                    "model_versions": [
                        {
                            "name": "fraud-model",
                            "version": "17",
                            "run_id": "candidate-run",
                        }
                    ]
                }
            ),
        ]

        details = self.make_client().get_model_details(
            "fraud-model", "candidate-run"
        )

        self.assertEqual(details["source"], "MLFLOW")
        self.assertEqual(details["model_version"], "17")
        self.assertEqual(details["metrics"], {"validation_pr_auc": 0.95})
        self.assertEqual(details["params"], {"decision_threshold": "0.61"})
        self.assertEqual(
            details["artifact_uri"],
            "mlflow-artifacts:/1/candidate-run/artifacts",
        )
        self.assertEqual(
            details["model_comparison_artifact_path"],
            "metadata/model-comparison.json",
        )

    @patch("app.services.mlops.mlflow.httpx.request")
    def test_alias_and_decision_tags_use_registry_endpoints(self, request: Mock) -> None:
        request.return_value = response({})
        client = self.make_client()

        client.set_model_alias("fraud-model", "champion", "17")
        client.set_model_version_tags(
            "fraud-model",
            "17",
            {"backend_decision": "APPROVE", "backend_decision_reason": "good"},
        )

        self.assertEqual(request.call_count, 3)
        self.assertTrue(request.call_args_list[0].args[1].endswith("/alias"))
        self.assertTrue(request.call_args_list[1].args[1].endswith("/set-tag"))

    def test_injected_http_client_is_reused_and_closed(self) -> None:
        http_client = Mock()
        http_client.request.return_value = response({"model_versions": []})
        client = MLflowRegistryClient(
            tracking_uri="https://mlflow.example",
            username="user",
            password="secret",
            timeout_seconds=5,
            http_client=http_client,
        )

        with self.assertRaises(MLflowRegistryError):
            client.resolve_model_version("fraud-model", "missing")
        with self.assertRaises(MLflowRegistryError):
            client.resolve_model_version("fraud-model", "missing-again")
        client.close()

        self.assertEqual(http_client.request.call_count, 2)
        http_client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
