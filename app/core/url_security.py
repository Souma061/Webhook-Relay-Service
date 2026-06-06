from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import settings


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-forwarded-authorization",
}


def redact_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    return {
        key: "***REDACTED***" if key.lower() in SENSITIVE_HEADER_NAMES else value
        for key, value in headers.items()
    }


def validate_delivery_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="route url must be an absolute http(s) URL")

    if scheme != "https" and not settings.allow_insecure_delivery_urls:
        raise HTTPException(status_code=400, detail="route url must use https")

    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="route url must not include credentials")

    if _is_blocked_host(parsed.hostname):
        raise HTTPException(status_code=400, detail="route url host is not allowed")

    return url


def validate_delivery_url_for_request(url: str) -> None:
    try:
        validate_delivery_url(url)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc

    if settings.allow_insecure_delivery_urls:
        return

    parsed = urlparse(url)
    assert parsed.hostname is not None

    for address in _resolve_host(parsed.hostname, parsed.port):
        if _is_blocked_ip(address):
            raise ValueError("route url resolves to a private or reserved address")


def _resolve_host(hostname: str, port: int | None) -> set[str]:
    results = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    return {result[4][0] for result in results}


def _is_blocked_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True

    try:
        return _is_blocked_ip(normalized)
    except ValueError:
        return False


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
