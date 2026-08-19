import unittest
from unittest.mock import Mock, patch

from app.services.client_registry import ServiceClientRegistry


class ServiceClientRegistryTest(unittest.TestCase):
    @patch("app.services.client_registry.CloudMonitoringClient")
    @patch("app.services.client_registry.CloudRunAdminClient")
    @patch("app.services.client_registry.MLflowRegistryClient")
    @patch("app.services.client_registry.MLServingClient")
    @patch("app.services.client_registry.httpx.Client")
    def test_clients_are_lazy_singletons_and_all_are_closed(
        self,
        http_client_type: Mock,
        serving_type: Mock,
        mlflow_type: Mock,
        cloud_run_type: Mock,
        monitoring_type: Mock,
    ) -> None:
        http_client_type.side_effect = [Mock(), Mock(), Mock(), Mock()]
        serving = serving_type.return_value
        mlflow = mlflow_type.return_value
        cloud_run = cloud_run_type.return_value
        monitoring = monitoring_type.return_value
        registry = ServiceClientRegistry()

        self.assertIs(registry.ml_serving(), registry.ml_serving())
        self.assertIs(registry.mlflow(), registry.mlflow())
        self.assertIs(registry.cloud_run(), registry.cloud_run())
        self.assertIs(registry.monitoring(), registry.monitoring())
        registry.close()

        serving_type.assert_called_once()
        mlflow_type.assert_called_once()
        cloud_run_type.assert_called_once()
        monitoring_type.assert_called_once()
        serving.close.assert_called_once_with()
        mlflow.close.assert_called_once_with()
        cloud_run.close.assert_called_once_with()
        monitoring.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
