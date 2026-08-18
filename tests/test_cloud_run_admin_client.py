import unittest
from copy import deepcopy
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


def cd_prepared_service(model_version: str = "17") -> dict:
    service = deepcopy(current_service())
    revision = "serving-00002-candidate"
    container = service["template"]["containers"][0]
    container["image"] = f"registry/serving@sha256:{'a' * 64}"
    container["env"] = [
        {"name": "ML_PREDICTOR_MODE", "value": "mlflow"},
        {"name": "ML_MODEL_NAME", "value": "fraud-model"},
        {"name": "ML_MODEL_VERSION", "value": model_version},
        {
            "name": "MLFLOW_TRACKING_PASSWORD",
            "valueSource": {
                "secretKeyRef": {"secret": "mlflow", "version": "latest"}
            },
        },
    ]
    service.update(
        {
            "etag": "etag-candidate",
            "latestCreatedRevision": revision,
            "latestReadyRevision": revision,
            "trafficStatuses": [
                {
                    "type": TRAFFIC_REVISION,
                    "revision": "serving-00001-old",
                    "percent": 100,
                },
                {
                    "type": TRAFFIC_REVISION,
                    "revision": revision,
                    "percent": 0,
                    "tag": f"model-v{model_version}",
                    "uri": f"https://model-v{model_version}---serving.run.app",
                },
            ],
        }
    )
    return service


def serving_revision(
    name: str,
    model_version: str,
    *,
    ready: bool = True,
) -> dict:
    return {
        "name": (
            "projects/test/locations/region/services/serving/"
            f"revisions/{name}"
        ),
        "reconciling": not ready,
        "conditions": [
            {
                "type": "Ready",
                "state": (
                    "CONDITION_SUCCEEDED" if ready else "CONDITION_PENDING"
                ),
            }
        ],
        "containers": [
            {
                "name": "serving",
                "image": f"registry/serving@sha256:{'a' * 64}",
                "env": [
                    {"name": "ML_PREDICTOR_MODE", "value": "mlflow"},
                    {"name": "ML_MODEL_NAME", "value": "fraud-model"},
                    {"name": "ML_MODEL_VERSION", "value": model_version},
                ],
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

    def test_injected_http_client_is_reused_and_closed(self) -> None:
        http_client = Mock()
        http_client.request.side_effect = [
            api_response({"name": "training"}),
            api_response({"name": "serving"}),
        ]
        client = self.make_client(http_client=http_client)

        client.get_training_status()
        client.get_serving_status()
        client.close()

        self.assertEqual(http_client.request.call_count, 2)
        http_client.close.assert_called_once_with()

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
    def test_stage_reuses_cd_prepared_ready_zero_traffic_revision(
        self,
        request: Mock,
    ) -> None:
        request.return_value = api_response(cd_prepared_service())
        client = self.make_client(serving_container="serving")

        result = client.verify_staged_model_revision("17")

        self.assertTrue(result["reused"])
        self.assertIsNone(result["operation"])
        self.assertEqual(result["tag"], "model-v17")
        self.assertEqual(result["revision"], "serving-00002-candidate")
        self.assertEqual(
            result["previousTraffic"],
            [
                {
                    "type": TRAFFIC_REVISION,
                    "revision": "serving-00001-old",
                    "percent": 100,
                }
            ],
        )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_accepts_omitted_zero_percent_from_cloud_run_v2(
        self,
        request: Mock,
    ) -> None:
        service = cd_prepared_service()
        service["trafficStatuses"][1].pop("percent")
        request.return_value = api_response(service)
        client = self.make_client(serving_container="serving")

        result = client.verify_staged_model_revision("17")

        self.assertTrue(result["reused"])
        self.assertEqual(result["revision"], "serving-00002-candidate")

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_rejects_duplicate_model_tag(self, request: Mock) -> None:
        service = cd_prepared_service()
        duplicate = deepcopy(service["trafficStatuses"][1])
        duplicate["revision"] = "serving-00003-duplicate"
        service["trafficStatuses"].append(duplicate)
        request.return_value = api_response(service)
        client = self.make_client(serving_container="serving")

        with self.assertRaisesRegex(CloudRunAdminError, "중복"):
            client.verify_staged_model_revision("17")

        self.assertEqual(request.call_count, 1)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_requires_cd_prepared_tag(self, request: Mock) -> None:
        request.return_value = api_response(current_service())
        client = self.make_client(serving_container="serving")

        with self.assertRaisesRegex(CloudRunAdminError, "ML Serving CD"):
            client.verify_staged_model_revision("17")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_rejects_cd_revision_with_mismatched_model_config(
        self,
        request: Mock,
    ) -> None:
        service = cd_prepared_service()
        env = service["template"]["containers"][0]["env"]
        next(item for item in env if item["name"] == "ML_MODEL_VERSION")["value"] = "99"
        request.return_value = api_response(service)
        client = self.make_client(serving_container="serving")

        with self.assertRaisesRegex(CloudRunAdminError, "모델 설정"):
            client.verify_staged_model_revision("17")

        self.assertEqual(request.call_count, 1)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_rejects_cd_revision_while_service_is_reconciling(
        self,
        request: Mock,
    ) -> None:
        service = cd_prepared_service()
        service["reconciling"] = True
        request.return_value = api_response(service)
        client = self.make_client(serving_container="serving")

        with self.assertRaisesRegex(CloudRunAdminError, "준비 중"):
            client.verify_staged_model_revision("17")

        self.assertEqual(request.call_count, 1)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_requires_latest_ready_revision_and_preserved_live_traffic(
        self,
        request: Mock,
    ) -> None:
        client = self.make_client(serving_container="serving")
        cases = (
            ("stale", "가장 최근"),
            ("not-ready", "Ready"),
            ("candidate-live", "운영 트래픽"),
        )

        for case, expected_message in cases:
            with self.subTest(case=case):
                service = cd_prepared_service()
                if case == "stale":
                    service["latestCreatedRevision"] = "serving-00003-other"
                    service["latestReadyRevision"] = "serving-00003-other"
                elif case == "not-ready":
                    service["latestReadyRevision"] = "serving-00001-old"
                else:
                    service["trafficStatuses"][0]["revision"] = (
                        "serving-00002-candidate"
                    )
                request.reset_mock()
                request.return_value = api_response(service)

                with self.assertRaisesRegex(CloudRunAdminError, expected_message):
                    client.verify_staged_model_revision("17")

                self.assertEqual(request.call_count, 1)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_stage_rejects_unpinned_image_or_nonzero_candidate_traffic(
        self,
        request: Mock,
    ) -> None:
        client = self.make_client(serving_container="serving")

        for field, expected_message in (
            ("image", "digest"),
            ("traffic", "0%"),
        ):
            with self.subTest(field=field):
                service = cd_prepared_service()
                if field == "image":
                    service["template"]["containers"][0]["image"] = (
                        "registry/serving:latest"
                    )
                else:
                    service["trafficStatuses"][0]["percent"] = 99
                    service["trafficStatuses"][1]["percent"] = 1
                request.reset_mock()
                request.return_value = api_response(service)

                with self.assertRaisesRegex(CloudRunAdminError, expected_message):
                    client.verify_staged_model_revision("17")

                self.assertEqual(request.call_count, 1)

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
            api_response(serving_revision("serving-00002-new", "17")),
            api_response(
                {"name": "projects/test/locations/region/operations/promote-op"}
            ),
        ]
        smoke_client = Mock()
        smoke_client.predict.return_value = MLPredictionResponse(
            transaction_id=900001,
            predict_result=0,
            predict_proba=0.1,
            model_name="fraud-model",
            model_version="17",
        )
        smoke_factory = Mock(return_value=smoke_client)
        client = self.make_client(smoke_client_factory=smoke_factory)

        result = client.promote_model_revision(
            model_version="17",
            transaction_id=900001,
            features={"Transaction_Amount": 1000},
        )

        self.assertEqual(result["revision"], "serving-00002-new")
        smoke_factory.assert_called_once_with(
            "https://model-v17---serving.run.app",
            "https://serving.run.app",
        )
        smoke_client.close.assert_called_once_with()
        patch_payload = request.call_args_list[2].kwargs["json"]
        self.assertEqual(
            patch_payload["traffic"],
            [
                {
                    "type": TRAFFIC_REVISION,
                    "revision": "serving-00002-new",
                    "percent": 100,
                    "tag": "model-v17",
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
                transaction_id=900001,
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
        request.side_effect = [
            api_response(service),
            api_response(serving_revision("serving-00017-new", "17")),
        ]
        client = self.make_client()

        result = client.get_model_deployment_status("17")

        self.assertTrue(result["ready"])
        self.assertEqual(result["revision"], "serving-00017-new")
        self.assertEqual(result["trafficPercent"], 100)
        self.assertEqual(
            request.call_args_list[1].args[1],
            (
                "https://run.googleapis.com/v2/projects/test/locations/region/"
                "services/serving/revisions/serving-00017-new"
            ),
        )

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_promote_revalidates_concrete_candidate_contract_before_smoke(
        self,
        request: Mock,
    ) -> None:
        service = cd_prepared_service()
        candidate = serving_revision("serving-00002-candidate", "17")
        candidate["containers"][0]["image"] = "registry/serving:latest"
        request.side_effect = [api_response(service), api_response(candidate)]
        smoke_factory = Mock()
        client = self.make_client(smoke_client_factory=smoke_factory)

        with self.assertRaisesRegex(CloudRunAdminError, "digest"):
            client.promote_model_revision(
                model_version="17",
                transaction_id=900001,
                features={},
            )

        smoke_factory.assert_not_called()
        self.assertEqual(request.call_count, 2)

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
        request.side_effect = [
            api_response(service),
            api_response(serving_revision("serving-00017-new", "1")),
        ]
        client = self.make_client()

        result = client.get_model_deployment_status("17")

        self.assertFalse(result["ready"])
        self.assertIn("모델 버전", result["reason"])

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_deployment_status_uses_live_revision_when_newer_zero_traffic_exists(
        self,
        request: Mock,
    ) -> None:
        service = cd_prepared_service(model_version="18")
        # v18 is the latest Ready candidate, but the approved v17 revision still
        # owns all traffic. Completion for v17 must inspect that concrete revision.
        service["trafficStatuses"][0]["revision"] = "serving-00017-live"
        service["trafficStatuses"][0]["percent"] = 100
        request.side_effect = [
            api_response(service),
            api_response(serving_revision("serving-00017-live", "17")),
        ]
        client = self.make_client()

        result = client.get_model_deployment_status("17")

        self.assertTrue(result["ready"])
        self.assertEqual(result["revision"], "serving-00017-live")
        self.assertEqual(result["revisionModelVersion"], "17")
        self.assertEqual(request.call_count, 2)

    @patch("app.services.mlops.cloud_run.httpx.request")
    def test_deployment_status_rejects_invalid_live_revision_contract(
        self,
        request: Mock,
    ) -> None:
        client = self.make_client()
        service = current_service()
        service["trafficStatuses"] = [
            {
                "type": TRAFFIC_REVISION,
                "revision": "serving-00017-live",
                "percent": 100,
            }
        ]

        for case, expected_message in (
            ("image", "digest"),
            ("threshold", "임계값"),
            ("mode", "mlflow"),
            ("name", "모델 이름"),
            ("ready", "Ready"),
        ):
            with self.subTest(case=case):
                revision = serving_revision("serving-00017-live", "17")
                container = revision["containers"][0]
                env = container["env"]
                if case == "image":
                    container["image"] = "registry/serving:latest"
                elif case == "threshold":
                    env.append({"name": "ML_FRAUD_THRESHOLD", "value": "0.5"})
                elif case == "mode":
                    env[0]["value"] = "fake"
                elif case == "name":
                    env[1]["value"] = "other-model"
                else:
                    revision = serving_revision(
                        "serving-00017-live",
                        "17",
                        ready=False,
                    )
                request.reset_mock()
                request.side_effect = [
                    api_response(deepcopy(service)),
                    api_response(revision),
                ]

                result = client.get_model_deployment_status("17")

                self.assertFalse(result["ready"])
                self.assertIn(expected_message, result["reason"])

    def test_revision_resource_rejects_path_injection(self) -> None:
        client = self.make_client()
        with self.assertRaisesRegex(CloudRunAdminError, "revision 이름"):
            client._revision_resource("../services/other")

    def test_deployment_tag_accepts_only_positive_ascii_version(self) -> None:
        client = self.make_client()
        self.assertEqual(client._deployment_tag("17"), "model-v17")
        for invalid in ("0", "01", "-1", "１７", "1.0"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(CloudRunAdminError, "ASCII"):
                    client._deployment_tag(invalid)

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
