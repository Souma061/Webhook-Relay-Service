import pytest
from fastapi import HTTPException

from app.core.url_security import redact_headers, validate_delivery_url


def test_validate_delivery_url_allows_https_public_host():
    assert validate_delivery_url("https://example.com/webhook") == "https://example.com/webhook"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/webhook",
        "https://localhost/webhook",
        "https://127.0.0.1/webhook",
        "https://10.0.0.10/webhook",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/webhook",
    ],
)
def test_validate_delivery_url_blocks_unsafe_urls(url):
    with pytest.raises(HTTPException):
        validate_delivery_url(url)


def test_redact_headers_masks_sensitive_values():
    assert redact_headers(
        {"Authorization": "Bearer secret", "Content-Type": "application/json"}
    ) == {"Authorization": "***REDACTED***", "Content-Type": "application/json"}
