import os
import unittest
from unittest.mock import Mock, patch

import httpx

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient

from app.services.mlops.monitoring import (
    CloudMonitoringClient,
    CloudMonitoringError,
    GceInstanceIdentity,
    get_cloud_monitoring_client,
    monitoring_alignment_seconds,
)
from main import app


def monitoring_response(*values: tuple[str, float]) -> Mock:
    response = Mock()
    response.json.return_value = {
        "timeSeries": [
            {
                "points": [
                    {
                        "interval": {"endTime": timestamp},
                        "value": {"doubleValue": value},
                    }
                    for timestamp, value in reversed(values)
                ]
            }
        ]
    }
    return response


class CloudMonitoringClientTest(unittest.TestCase):
    def make_client(self, http_client: Mock) -> CloudMonitoringClient:
        return CloudMonitoringClient(
            project_id="test-project",
            region="asia-northeast3",
            serving_service="fdshield-ml-serving",
            training_job="fdshield-training",
            token_provider=lambda: "access-token",
            instance_identity_provider=lambda: GceInstanceIdentity(
                instance_id="123",
                instance_name="fdshield-backend",
                zone="asia-northeast3-a",
            ),
            http_client=http_client,
        )

    def test_serving_metrics_are_summarized_in_time_order(self) -> None:
        first = "2026-08-19T03:00:00Z"
        second = "2026-08-19T03:01:00Z"
        http_client = Mock()
        http_client.get.side_effect = [
            monitoring_response((first, 10), (second, 20)),
            monitoring_response((first, 1), (second, 2)),
            monitoring_response((first, 100), (second, 120)),
            monitoring_response((first, 140), (second, 180)),
            monitoring_response((first, 20), (second, 30)),
            monitoring_response((first, 1), (second, 2)),
            monitoring_response((first, 0), (second, 1)),
            monitoring_response((first, 0.2), (second, 0.4)),
            monitoring_response((first, 0.5), (second, 0.6)),
        ]
        client = self.make_client(http_client)

        result = client.get_serving_metrics(60)

        self.assertEqual(result["summary"]["request_count"], 30)
        self.assertEqual(result["summary"]["error_rate_percent"], 10)
        self.assertEqual(result["summary"]["p95_latency_ms"], 120)
        self.assertEqual(result["summary"]["p99_latency_ms"], 180)
        self.assertEqual(result["summary"]["pending_p95_latency_ms"], 30)
        self.assertEqual(result["summary"]["active_instances"], 2)
        self.assertEqual(result["summary"]["idle_instances"], 1)
        self.assertEqual(result["summary"]["cpu_utilization_percent"], 40)
        self.assertEqual(result["summary"]["memory_utilization_percent"], 60)
        self.assertEqual(
            result["series"]["request_count"][0]["timestamp"],
            first,
        )
        self.assertEqual(
            result["series"]["error_rate_percent"][1]["value"],
            10,
        )
        self.assertEqual(http_client.get.call_count, 9)
        request_params = http_client.get.call_args_list[0].kwargs["params"]
        self.assertIn("fdshield-ml-serving", request_params["filter"])
        self.assertEqual(
            request_params["aggregation.alignmentPeriod"],
            "60s",
        )

    def test_daily_window_uses_fifteen_minute_alignment(self) -> None:
        http_client = Mock()
        http_client.get.side_effect = [monitoring_response() for _ in range(9)]
        client = self.make_client(http_client)

        result = client.get_serving_metrics(1440)

        self.assertEqual(result["alignment_seconds"], 900)
        request_params = http_client.get.call_args_list[0].kwargs["params"]
        self.assertEqual(request_params["aggregation.alignmentPeriod"], "900s")
        self.assertEqual(monitoring_alignment_seconds(360), 300)

    def test_training_metrics_use_job_resource(self) -> None:
        timestamp = "2026-08-19T03:00:00Z"
        http_client = Mock()
        http_client.get.side_effect = [
            monitoring_response((timestamp, 1)),
            monitoring_response((timestamp, 2)),
            monitoring_response((timestamp, 0.3)),
            monitoring_response((timestamp, 0.4)),
            monitoring_response((timestamp, 75)),
        ]
        client = self.make_client(http_client)

        result = client.get_training_metrics(60)

        self.assertEqual(result["summary"]["running_executions"], 1)
        self.assertEqual(result["summary"]["completed_executions"], 2)
        self.assertEqual(result["summary"]["cpu_utilization_percent"], 30)
        self.assertEqual(result["summary"]["billable_instance_seconds"], 75)
        request_params = http_client.get.call_args_list[0].kwargs["params"]
        self.assertIn('resource.type = "cloud_run_job"', request_params["filter"])
        self.assertIn("fdshield-training", request_params["filter"])

    def test_platform_metrics_identify_current_vm(self) -> None:
        timestamp = "2026-08-19T03:00:00Z"
        http_client = Mock()
        http_client.get.side_effect = [
            monitoring_response((timestamp, 0.25)),
            monitoring_response((timestamp, 42)),
            monitoring_response((timestamp, 61)),
        ]
        client = self.make_client(http_client)

        result = client.get_platform_metrics(60)

        self.assertEqual(result["instance_name"], "fdshield-backend")
        self.assertEqual(result["summary"]["cpu_utilization_percent"], 25)
        self.assertEqual(result["summary"]["memory_utilization_percent"], 42)
        self.assertTrue(result["ops_agent_available"])
        request_params = http_client.get.call_args_list[0].kwargs["params"]
        self.assertIn('resource.labels.instance_id = "123"', request_params["filter"])

    def test_http_error_is_translated(self) -> None:
        http_client = Mock()
        http_client.get.side_effect = httpx.ConnectError("offline")
        client = self.make_client(http_client)

        with self.assertRaisesRegex(CloudMonitoringError, "시계열 조회"):
            client.get_serving_metrics(60)

    def test_injected_http_client_is_closed(self) -> None:
        http_client = Mock()
        client = self.make_client(http_client)

        client.close()

        http_client.close.assert_called_once_with()


class CloudMonitoringApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.monitoring = Mock()
        app.dependency_overrides[get_cloud_monitoring_client] = lambda: self.monitoring
        self.client = TestClient(app)
        self.headers = {"X-MLOps-Admin-Token": "admin-secret"}

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_monitoring_endpoint_returns_cloud_metrics(self) -> None:
        self.monitoring.get_serving_metrics.return_value = {
            "window_minutes": 60,
            "alignment_seconds": 60,
            "data_delay_seconds": 120,
            "service_name": "fdshield-ml-serving",
            "region": "asia-northeast3",
            "queried_at": "2026-08-19T03:02:00Z",
            "latest_sample_at": "2026-08-19T03:01:00Z",
            "summary": {
                "request_count": 30,
                "error_rate_percent": 10,
                "p95_latency_ms": 120,
                "p99_latency_ms": 180,
                "pending_p95_latency_ms": 30,
                "active_instances": 2,
                "idle_instances": 1,
                "cpu_utilization_percent": 40,
                "memory_utilization_percent": 60,
            },
            "series": {
                "request_count": [],
                "error_rate_percent": [],
                "p95_latency_ms": [],
                "p99_latency_ms": [],
                "pending_p95_latency_ms": [],
                "active_instances": [],
                "cpu_utilization_percent": [],
                "memory_utilization_percent": [],
            },
        }

        response = self.client.get(
            "/mlops/serving/monitoring?window_minutes=60",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["active_instances"], 2)
        self.monitoring.get_serving_metrics.assert_called_once_with(60)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_training_monitoring_endpoint_returns_job_metrics(self) -> None:
        self.monitoring.get_training_metrics.return_value = {
            "window_minutes": 360,
            "alignment_seconds": 300,
            "data_delay_seconds": 120,
            "job_name": "fdshield-training",
            "region": "asia-northeast3",
            "queried_at": "2026-08-19T03:02:00Z",
            "latest_sample_at": "2026-08-19T03:01:00Z",
            "summary": {
                "running_executions": 1,
                "completed_executions": 2,
                "cpu_utilization_percent": 30,
                "memory_utilization_percent": 40,
                "billable_instance_seconds": 75,
            },
            "series": {
                "running_executions": [],
                "completed_executions": [],
                "cpu_utilization_percent": [],
                "memory_utilization_percent": [],
                "billable_instance_seconds": [],
            },
        }

        response = self.client.get(
            "/mlops/training/monitoring?window_minutes=360",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["completed_executions"], 2)
        self.monitoring.get_training_metrics.assert_called_once_with(360)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_platform_monitoring_endpoint_returns_vm_metrics(self) -> None:
        self.monitoring.get_platform_metrics.return_value = {
            "window_minutes": 1440,
            "alignment_seconds": 900,
            "data_delay_seconds": 240,
            "instance_id": "123",
            "instance_name": "fdshield-backend",
            "zone": "asia-northeast3-a",
            "queried_at": "2026-08-19T03:02:00Z",
            "latest_sample_at": "2026-08-19T03:00:00Z",
            "ops_agent_available": True,
            "summary": {
                "cpu_utilization_percent": 25,
                "memory_utilization_percent": 42,
                "disk_utilization_percent": 61,
            },
            "series": {
                "cpu_utilization_percent": [],
                "memory_utilization_percent": [],
                "disk_utilization_percent": [],
            },
        }

        response = self.client.get(
            "/mlops/platform/monitoring?window_minutes=1440",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["instance_name"], "fdshield-backend")
        self.monitoring.get_platform_metrics.assert_called_once_with(1440)

    @patch("app.api.mlops.config.MLOPS_ADMIN_TOKEN", "admin-secret")
    def test_monitoring_error_uses_upstream_response(self) -> None:
        self.monitoring.get_serving_metrics.side_effect = CloudMonitoringError(
            "Monitoring 권한이 없습니다."
        )

        response = self.client.get(
            "/mlops/serving/monitoring",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"]["message"],
            "Monitoring 권한이 없습니다.",
        )


if __name__ == "__main__":
    unittest.main()
