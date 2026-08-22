"""운영 VM이 외부에 제공하는 HTTPS 인증서 상태를 확인한다."""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime
from functools import lru_cache


class HttpsCertificateError(RuntimeError):
    """HTTPS 인증서를 읽지 못했거나 만료일이 없는 경우."""


@lru_cache(maxsize=2)
def _read_https_certificate_expiry(host: str, hour_key: str) -> datetime:
    del hour_key
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=3) as connection:
            with context.wrap_socket(connection, server_hostname=host) as tls:
                certificate = tls.getpeercert()
        not_after = certificate.get("notAfter")
        if not isinstance(not_after, str):
            raise HttpsCertificateError("HTTPS 인증서 만료일이 비어 있습니다.")
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise HttpsCertificateError(
            "HTTPS 인증서를 확인하지 못했습니다."
        ) from exc


def get_https_certificate_expiry(host: str) -> datetime:
    """실제 제공 중인 인증서를 읽되 네트워크 확인은 한 시간 동안 재사용한다."""

    if not host:
        raise HttpsCertificateError("HTTPS 인증서 확인 도메인이 비어 있습니다.")
    hour_key = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    return _read_https_certificate_expiry(host, hour_key)


__all__ = ["HttpsCertificateError", "get_https_certificate_expiry"]
