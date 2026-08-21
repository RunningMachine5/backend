import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from app.services.mlops.platform_health import (
    _read_https_certificate_expiry,
    get_https_certificate_expiry,
)


class HttpsCertificateMonitorTest(unittest.TestCase):
    def tearDown(self) -> None:
        _read_https_certificate_expiry.cache_clear()

    @patch("app.services.mlops.platform_health.ssl.cert_time_to_seconds")
    @patch("app.services.mlops.platform_health.ssl.create_default_context")
    @patch("app.services.mlops.platform_health.socket.create_connection")
    def test_reads_expiry_from_served_certificate(
        self,
        create_connection: Mock,
        create_default_context: Mock,
        cert_time_to_seconds: Mock,
    ) -> None:
        connection = create_connection.return_value.__enter__.return_value
        tls = (
            create_default_context.return_value
            .wrap_socket.return_value.__enter__.return_value
        )
        tls.getpeercert.return_value = {
            "notAfter": "Oct 20 00:00:00 2026 GMT"
        }
        cert_time_to_seconds.return_value = datetime(
            2026, 10, 20, tzinfo=UTC
        ).timestamp()

        expires_at = get_https_certificate_expiry("api.fdshield.cloud")

        self.assertEqual(expires_at, datetime(2026, 10, 20, tzinfo=UTC))
        create_default_context.return_value.wrap_socket.assert_called_once_with(
            connection,
            server_hostname="api.fdshield.cloud",
        )


if __name__ == "__main__":
    unittest.main()
