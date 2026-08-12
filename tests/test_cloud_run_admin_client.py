import unittest
from unittest.mock import Mock, patch

import httpx

from app.services.ml_serving.client import MLPredictionResponse
from app.services.mlops.cloud_run import (
    TRAFFIC_LATEST,
    TRAFFIC_REVISION,
    CloudRunAdminClient,
    CloudRunAdminError,
)


def api_response(payload: dict) -> Mock:
    response = Mock()
    response.json.return_value = payload
    return response


def current_service() -> dict:
    return {
        "name": "projects/test/locations/region/services/serving",
        "etag": "etag-old",
        "uri": "https://serving.run.app",
        "latestCreatedRevision": "serving-00001-old",
        "latestReadyRevision": "serving-00001-old",
        "reconciling": False,
        "template": {
            "revision": "serving-00001-old",
            "serviceAccount": "serving@test.iam.gserviceaccount.com",
            "containers": [
                {
                    "name": "serving",
                    "image": "registry/serving@sha256:abc",
                    "buildInfo": {"sourceLocation": "gs://output-only"},
                    "env": [
                        {"name": "ML_MODEL_VERSION", "value": "1"},
                        {"name": "ML_FRAUD_THRESHOLD", "value": "0.55"},
                        {
                            "name": "MLFLOW_TRACKING_PASSWORD",
                            "valueSource": {
                                "secretKeyRef": {"secret": "mlflow", "version": "latest"}
                            },
                        },
                    ],
                }
            ],
        },
        "trafficStatuses": [
            {
                "type": TRAFFIC_REVISION,
                "revision": "serving-00001-old",
                "percent": 100,
            }
        ],
    }


class CloudRunAdminClientTest(unittest.TestCase):
    def make_client(self, **kwargs) -> CloudRunAdminClient:
        return CloudRunAdminClient(
            project_id="test",
            region="region",
            training_job="training",
            serving_service="serving",
            model_name="fraud-model",
            token_provider=lambda: "access-token",
            **kwargs,
        )

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_uses_job_override_without_changing_job(self, request: Mock) -> None:
        request.return_value = api_response(
            {"name": "projects/test/locations/region/operations/train-op"}
        )
        client = self.make_client()

        result = client.run_training(
            min_pr_auc=0.75,
            min_recall=0.8,
            dataset_uri="gs://bucket/transactions.csv",
            training_run_id=12,
        )

        self.assertTrue(result["name"].endswith("/train-op"))
        call = request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertTrue(call.args[1].endswith("/jobs/training:run"))
        env = call.kwargs["json"]["overrides"]["containerOverrides"][0]["env"]
        env_by_name = {item["name"]: item["value"] for item in env}
        self.assertEqual(env_by_name["TRAINING_MODE"], "train")
        self.assertEqual(
            env_by_name["TRAINING_DATA_URI"],
            "gs://bucket/transactions.csv",
        )
        self.assertEqual(env_by_name["BACKEND_TRAINING_RUN_ID"], "12")
        self.assertNotIn("TRAINING_SPLIT_DATETIME", env_by_name)
        self.assertNotIn("CHAMPION_MODEL_VERSION", env_by_name)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_omits_optional_dataset_overrides(self, request: Mock) -> None:
        request.return_value = api_response(
            {"name": "projects/test/locations/region/operations/train-op"}
        )
        client = self.make_client()

        client.run_training(
            min_pr_auc=0.0,
            min_recall=0.0,
        )

        env = request.call_args.kwargs["json"]["overrides"]["containerOverrides"][0][
            "env"
        ]
        env_names = {item["name"] for item in env}
        self.assertNotIn("TRAINING_DATA_URI", env_names)
        self.assertNotIn("TRAINING_SPLIT_DATETIME", env_names)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_accepts_raw_dataset_without_companion(
        self,
        request: Mock,
    ) -> None:
        request.return_value = api_response(
            {"name": "projects/test/locations/region/operations/train-op"}
        )
        client = self.make_client()

        client.run_training(
            min_pr_auc=0.0,
            min_recall=0.0,
            dataset_uri="gs://bucket/transactions.csv",
        )

        env = request.call_args.kwargs["json"]["overrides"]["containerOverrides"][0][
            "env"
        ]
        env_by_name = {item["name"]: item["value"] for item in env}
        self.assertEqual(
            env_by_name["TRAINING_DATA_URI"],
            "gs://bucket/transactions.csv",
        )

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_timeout_preserves_unknown_acceptance(
        self,
        request: Mock,
    ) -> None:
        request.side_effect = httpx.ReadTimeout(
            "response timeout",
            request=httpx.Request("POST", "https://run.googleapis.com/v2/job:run"),
        )

        with self.assertRaises(CloudRunAdminError) as raised:
            self.make_client().run_training(min_pr_auc=0.0, min_recall=0.0)

        self.assertIsNone(raised.exception.status_code)
        self.assertTrue(raised.exception.request_may_have_been_accepted)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_5xx_preserves_unknown_acceptance(
        self,
        request: Mock,
    ) -> None:
        request.return_value = httpx.Response(
            503,
            request=httpx.Request("POST", "https://run.googleapis.com/v2/job:run"),
            json={"error": {"code": 503}},
        )

        with self.assertRaises(CloudRunAdminError) as raised:
            self.make_client().run_training(min_pr_auc=0.0, min_recall=0.0)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(raised.exception.request_may_have_been_accepted)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_definitive_4xx_is_not_marked_as_accepted(
        self,
        request: Mock,
    ) -> None:
        request.return_value = httpx.Response(
            400,
            request=httpx.Request("POST", "https://run.googleapis.com/v2/job:run"),
            json={"error": {"code": 400}},
        )

        with self.assertRaises(CloudRunAdminError) as raised:
            self.make_client().run_training(min_pr_auc=0.0, min_recall=0.0)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(raised.exception.request_may_have_been_accepted)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_run_training_malformed_success_preserves_unknown_acceptance(
        self,
        request: Mock,
    ) -> None:
        request.return_value = httpx.Response(
            200,
            request=httpx.Request("POST", "https://run.googleapis.com/v2/job:run"),
            content=b"not-json",
        )

        with self.assertRaises(CloudRunAdminError) as raised:
            self.make_client().run_training(min_pr_auc=0.0, min_recall=0.0)

        self.assertEqual(raised.exception.status_code, 200)
        self.assertTrue(raised.exception.request_may_have_been_accepted)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_create_revision_pins_current_traffic_and_preserves_secret_env(
        self,
        request: Mock,
    ) -> None:
        request.side_effect = [
            api_response(current_service()),
            api_response(
                {"name": "projects/test/locations/region/operations/deploy-op"}
            ),
        ]
        client = self.make_client(serving_container="serving")

        result = client.create_model_revision("17")

        self.assertEqual(result["tag"], "model-v17")
        patch_call = request.call_args_list[1]
        self.assertEqual(patch_call.args[0], "PATCH")
        payload = patch_call.kwargs["json"]
        self.assertNotIn("revision", payload["template"])
        container = payload["template"]["containers"][0]
        self.assertEqual(container["image"], "registry/serving@sha256:abc")
        self.assertNotIn("buildInfo", container)
        env_by_name = {item["name"]: item for item in container["env"]}
        self.assertIn("valueSource", env_by_name["MLFLOW_TRACKING_PASSWORD"])
        self.assertEqual(env_by_name["ML_MODEL_VERSION"]["value"], "17")
        self.assertNotIn("ML_FRAUD_THRESHOLD", env_by_name)
        self.assertEqual(payload["traffic"][0]["revision"], "serving-00001-old")
        self.assertEqual(payload["traffic"][0]["percent"], 100)
        self.assertEqual(payload["traffic"][1]["type"], TRAFFIC_LATEST)
        self.assertEqual(payload["traffic"][1]["percent"], 0)
        self.assertEqual(payload["traffic"][1]["tag"], "model-v17")

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_promote_resolves_latest_tag_then_smoke_tests_and_moves_traffic(
        self,
        request: Mock,
    ) -> None:
        service = current_service()
        service.update(
            {
                "etag": "etag-new",
                "latestCreatedRevision": (
                    "projects/test/locations/region/services/serving/"
                    "revisions/serving-00002-new"
                ),
                "latestReadyRevision": (
                    "projects/test/locations/region/services/serving/"
                    "revisions/serving-00002-new"
                ),
                "trafficStatuses": [
                    {
                        "type": TRAFFIC_REVISION,
                        "revision": "serving-00001-old",
                        "percent": 100,
                    },
                    {
                        "type": TRAFFIC_LATEST,
                        "percent": 0,
                        "tag": "model-v17",
                        "uri": "https://model-v17---serving.run.app",
                    },
                ],
            }
        )
        request.side_effect = [
            api_response(service),
            api_response(
                {"name": "projects/test/locations/region/operations/promote-op"}
            ),
        ]
        smoke_client = Mock()
        smoke_client.predict.return_value = MLPredictionResponse(
            transaction_id="TX-SMOKE",
            is_fraud=False,
            fraud_probability=0.1,
            model_name="fraud-model",
            model_version="17",
        )
        smoke_factory = Mock(return_value=smoke_client)
        client = self.make_client(smoke_client_factory=smoke_factory)

        result = client.promote_model_revision(
            model_version="17",
            transaction_id="TX-SMOKE",
            features={"Transaction_Amount": 1000},
        )

        self.assertEqual(result["revision"], "serving-00002-new")
        smoke_factory.assert_called_once_with(
            "https://model-v17---serving.run.app",
            "https://serving.run.app",
        )
        patch_payload = request.call_args_list[1].kwargs["json"]
        self.assertEqual(
            patch_payload["traffic"],
            [
                {
                    "type": TRAFFIC_REVISION,
                    "revision": "serving-00002-new",
                    "percent": 100,
                }
            ],
        )

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_promote_rejects_not_ready_revision_without_smoke_or_patch(
        self,
        request: Mock,
    ) -> None:
        service = current_service()
        service.update(
            {
                "latestCreatedRevision": "serving-00002-new",
                "latestReadyRevision": "serving-00001-old",
                "trafficStatuses": [
                    {
                        "revision": "serving-00002-new",
                        "percent": 0,
                        "tag": "model-v17",
                        "uri": "https://model-v17---serving.run.app",
                    }
                ],
            }
        )
        request.return_value = api_response(service)
        smoke_factory = Mock()
        client = self.make_client(smoke_client_factory=smoke_factory)

        with self.assertRaisesRegex(CloudRunAdminError, "Ready"):
            client.promote_model_revision(
                model_version="17",
                transaction_id="TX-SMOKE",
                features={},
            )

        smoke_factory.assert_not_called()
        self.assertEqual(request.call_count, 1)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_deployment_status_requires_ready_revision_version_and_full_traffic(
        self,
        request: Mock,
    ) -> None:
        service = current_service()
        service.update(
            {
                "latestCreatedRevision": "serving-00017-new",
                "latestReadyRevision": "serving-00017-new",
                "template": {
                    **service["template"],
                    "containers": [
                        {
                            **service["template"]["containers"][0],
                            "env": [
                                {"name": "ML_MODEL_VERSION", "value": "17"},
                            ],
                        }
                    ],
                },
                "trafficStatuses": [
                    {
                        "type": TRAFFIC_REVISION,
                        "revision": "serving-00017-new",
                        "percent": 100,
                    }
                ],
            }
        )
        request.return_value = api_response(service)
        client = self.make_client()

        result = client.get_model_deployment_status("17")

        self.assertTrue(result["ready"])
        self.assertEqual(result["revision"], "serving-00017-new")
        self.assertEqual(result["trafficPercent"], 100)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_deployment_status_rejects_wrong_revision_model_env(
        self,
        request: Mock,
    ) -> None:
        service = current_service()
        service.update(
            {
                "latestCreatedRevision": "serving-00017-new",
                "latestReadyRevision": "serving-00017-new",
                "trafficStatuses": [
                    {
                        "type": TRAFFIC_REVISION,
                        "revision": "serving-00017-new",
                        "percent": 100,
                    }
                ],
            }
        )
        request.return_value = api_response(service)
        client = self.make_client()

        result = client.get_model_deployment_status("17")

        self.assertFalse(result["ready"])
        self.assertIn("모델 버전", result["reason"])

    def test_operation_id_rejects_resource_path_injection(self) -> None:
        client = self.make_client()
        with self.assertRaisesRegex(CloudRunAdminError, "operation ID"):
            client.get_operation("../../services/serving")

    def test_training_execution_name_never_uses_lro_operation_name(self) -> None:
        operation = {
            "name": "projects/test/locations/region/operations/train-op",
            "metadata": {
                "target": (
                    "projects/test/locations/region/jobs/training/"
                    "executions/training-abc"
                )
            },
        }
        self.assertEqual(
            CloudRunAdminClient.training_execution_name(operation),
            "training-abc",
        )
        self.assertIsNone(
            CloudRunAdminClient.training_execution_name(
                {"name": "projects/test/locations/region/operations/train-op"}
            )
        )

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_training_execution_live_status_and_outcome(
        self,
        request: Mock,
    ) -> None:
        execution = {
            "name": (
                "projects/test/locations/region/jobs/training/"
                "executions/training-abc"
            ),
            "terminalCondition": {"state": "CONDITION_FAILED"},
            "failedCount": 1,
        }
        request.return_value = api_response(execution)

        result = self.make_client().get_training_execution("training-abc")

        self.assertEqual(result, execution)
        self.assertTrue(
            request.call_args.args[1].endswith(
                "/jobs/training/executions/training-abc"
            )
        )
        self.assertEqual(
            CloudRunAdminClient.training_execution_outcome(result),
            "FAILED",
        )
        self.assertEqual(
            CloudRunAdminClient.training_execution_outcome(
                {"terminalCondition": {"state": "CONDITION_SUCCEEDED"}}
            ),
            "SUCCEEDED",
        )
        self.assertEqual(
            CloudRunAdminClient.training_execution_outcome(
                {"succeededCount": 1, "completionTime": "2026-08-11T00:00:00Z"}
            ),
            "SUCCEEDED",
        )
        self.assertEqual(
            CloudRunAdminClient.training_execution_outcome(
                {"terminalCondition": {"state": "CONDITION_RECONCILING"}}
            ),
            "RUNNING",
        )

        with self.assertRaisesRegex(CloudRunAdminError, "execution 이름"):
            self.make_client().get_training_execution("../other-job")


if __name__ == "__main__":
    unittest.main()
