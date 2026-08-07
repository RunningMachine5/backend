import unittest
from unittest.mock import Mock, patch

from app.services.ml_serving.client import MLPredictionResponse
from app.services.mlops.cloud_run import (
    CloudRunAdminClient,
    CloudRunAdminError,
    TRAFFIC_LATEST,
    TRAFFIC_REVISION,
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
            auto_promote=True,
            min_pr_auc=0.75,
            min_recall=0.8,
            dataset_uri="gs://bucket/train.csv",
        )

        self.assertTrue(result["name"].endswith("/train-op"))
        call = request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertTrue(call.args[1].endswith("/jobs/training:run"))
        env = call.kwargs["json"]["overrides"]["containerOverrides"][0]["env"]
        env_by_name = {item["name"]: item["value"] for item in env}
        self.assertEqual(env_by_name["TRAINING_MODE"], "train")
        self.assertEqual(env_by_name["TRAINING_DATA_URI"], "gs://bucket/train.csv")
        self.assertEqual(env_by_name["MLFLOW_AUTO_PROMOTE"], "true")

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
        self.assertEqual(payload["traffic"][0]["revision"], "serving-00001-old")
        self.assertEqual(payload["traffic"][0]["percent"], 100)
        self.assertEqual(payload["traffic"][1]["type"], TRAFFIC_LATEST)
        self.assertEqual(payload["traffic"][1]["percent"], 0)
        self.assertEqual(payload["traffic"][1]["tag"], "model-v17")

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_promote_smoke_tests_exact_model_then_moves_all_traffic(
        self,
        request: Mock,
    ) -> None:
        service = current_service()
        service.update(
            {
                "etag": "etag-new",
                "latestCreatedRevision": "serving-00002-new",
                "latestReadyRevision": "serving-00002-new",
                "trafficStatuses": [
                    {
                        "type": TRAFFIC_REVISION,
                        "revision": "serving-00001-old",
                        "percent": 100,
                    },
                    {
                        "type": TRAFFIC_LATEST,
                        "revision": "serving-00002-new",
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

    def test_operation_id_rejects_resource_path_injection(self) -> None:
        client = self.make_client()
        with self.assertRaisesRegex(CloudRunAdminError, "operation ID"):
            client.get_operation("../../services/serving")


if __name__ == "__main__":
    unittest.main()
