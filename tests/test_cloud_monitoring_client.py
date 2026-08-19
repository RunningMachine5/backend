import os
import unittest
from unittest.mock import Mock, patch

import httpx

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")

from fastapi.testclient import TestClient

from app.services.mlops.monitoring import (
    CloudMonitoringClient,
    CloudMonitoringError,
    get_cloud_monitoring_client,
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
            token_provider=lambda: "access-token",
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
        self.assertEqual(result["summary"]["active_instances"], 2)
        self.assertEqual(result["summary"]["idle_instances"], 1)
        self.assertEqual(result["summary"]["cpu_utilization_percent"], 40)
        self.assertEqual(result["summary"]["memory_utilization_percent"], 60)
        self.assertEqual(
            result["series"]["requests_per_minute"][0]["timestamp"],
            first,
        )
        self.assertEqual(
            result["series"]["error_rate_percent"][1]["value"],
            10,
        )
        self.assertEqual(http_client.get.call_count, 7)
        request_params = http_client.get.call_args_list[0].kwargs["params"]
        self.assertIn("fdshield-ml-serving", request_params["filter"])
        self.assertEqual(
            request_params["aggregation.alignmentPeriod"],
            "60s",
        )

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
                "active_instances": 2,
                "idle_instances": 1,
                "cpu_utilization_percent": 40,
                "memory_utilization_percent": 60,
            },
            "series": {
                "requests_per_minute": [],
                "error_rate_percent": [],
                "p95_latency_ms": [],
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
