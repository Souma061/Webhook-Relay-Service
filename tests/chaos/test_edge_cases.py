"""Edge case tests: malformed payloads, boundary values, DoS vectors.

Tests ensure the gateway rejects invalid input gracefully without
crashing, leaking memory, or corrupting state.
"""

import hashlib
import hmac
import json
import uuid
import pytest


# ============================================================
#  BODY SIZE BOUNDARIES
# ============================================================

class TestBodySizeBoundaries:
    """Payloads at, near, and beyond the 1MB size limit."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_body_just_under_limit(self, client, live_endpoint):
        """1MB - 1 byte → should be accepted."""
        from tests.chaos.conftest import sign_payload
        size = 1024 * 1024 - 1
        payload = json.dumps({"data": "x" * (size - 20)}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code in (202, 413), f"Unexpected: {resp.status_code}"

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_body_exactly_at_limit(self, client, live_endpoint):
        """Exactly 1MB → may be accepted or rejected (depends on streaming overhead)."""
        from tests.chaos.conftest import sign_payload
        size = 1024 * 1024
        payload = json.dumps({"data": "x" * (size - 20)}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code in (202, 413)

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_body_one_byte_over_limit(self, client, live_endpoint):
        """1MB + 1 byte → must be rejected with 413."""
        from tests.chaos.conftest import sign_payload
        size = 1024 * 1024 + 1
        payload = json.dumps({"data": "x" * (size - 20)}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_body_10x_over_limit(self, client, live_endpoint):
        """10MB payload → must be rejected."""
        from tests.chaos.conftest import sign_payload
        payload = b"x" * (10 * 1024 * 1024)
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 413

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_empty_body(self, client, live_endpoint):
        """0-byte body → should be 400 (invalid JSON)."""
        from tests.chaos.conftest import sign_payload
        sig = sign_payload(live_endpoint["secret"], b"")
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=b"",
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 400


# ============================================================
#  MALFORMED PAYLOADS
# ============================================================

class TestMalformedPayloads:
    """Payloads that are technically JSON but structured to break parsers."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_deeply_nested_json(self, client, live_endpoint):
        """100 levels of nested objects → should parse and be accepted."""
        from tests.chaos.conftest import sign_payload
        data = {}
        current = data
        for _ in range(100):
            current["x"] = {}
            current = current["x"]
        payload = json.dumps(data).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_huge_json_array(self, client, live_endpoint):
        """Array with 100,000 integers → should be accepted."""
        from tests.chaos.conftest import sign_payload
        data = list(range(100_000))
        payload = json.dumps(data).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_unicode_bomb(self, client, live_endpoint):
        """Payload with multi-byte unicode characters."""
        from tests.chaos.conftest import sign_payload
        payload = json.dumps({"text": "你好世界🔥💥" * 1000}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_body_with_null_bytes(self, client, live_endpoint):
        """JSON string containing \\u0000 → may be rejected or accepted."""
        from tests.chaos.conftest import sign_payload
        payload = json.dumps({"data": "null\x00byte"}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code in (202, 400)


# ============================================================
#  SIGNATURE ATTACKS
# ============================================================

class TestSignatureAttacks:
    """Signature edge cases — timing attacks, format bugs."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_signature_without_prefix(self, client, live_endpoint):
        """Signature missing 'sha256=' prefix → 401."""
        from tests.chaos.conftest import sign_payload
        payload = b'{"a": 1}'
        sig = sign_payload(live_endpoint["secret"], payload).replace("sha256=", "")
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 401

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_signature_with_extra_prefix(self, client, live_endpoint):
        """Signature with double prefix → 401."""
        from tests.chaos.conftest import sign_payload
        payload = b'{"a": 1}'
        sig = "sha256=" + sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 401

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_empty_signature(self, client, live_endpoint):
        """Empty signature string → 401."""
        payload = b'{"a": 1}'
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={"x-hub-signature-256": ""},
        )
        assert resp.status_code == 401


# ============================================================
#  URL / ENDPOINT ID INJECTION
# ============================================================

class TestEndpointIdInjection:
    """Malformed endpoint IDs — injection attempts, type confusion."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_sql_injection_in_endpoint_id(self, client):
        """SQL-like strings in endpoint_id → should fail with 400."""
        payloads = [
            "'; DROP TABLE events; --",
            "' OR '1'='1",
            "../etc/passwd",
            "<script>alert('xss')</script>",
        ]
        for bad_id in payloads:
            resp = await client.post(
                f"/hooks/{bad_id}",
                content=b"{}",
                headers={"x-hub-signature-256": "sha256=abc"},
            )
            assert resp.status_code == 400, f"ID {bad_id!r} returned {resp.status_code}"

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_oversized_endpoint_id(self, client):
        """10KB endpoint ID → should return 400."""
        big_id = "a" * 10_000
        resp = await client.post(
            f"/hooks/{big_id}",
            content=b"{}",
            headers={"x-hub-signature-256": "sha256=abc"},
        )
        assert resp.status_code in (400, 413, 422)


# ============================================================
#  DOS VECTORS
# ============================================================

class TestDosVectors:
    """Basic DoS resilience — rapid requests, connection exhaustion."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_rapid_fire_invalid_requests(self, client, live_endpoint):
        """1,000 rapid-fire requests with bad signatures → all 401, no crash."""
        results = await asyncio.gather(*[
            client.post(
                f"/hooks/{live_endpoint['id']}",
                content=b"{}",
                headers={"x-hub-signature-256": "sha256=deadbeef"},
            )
            for _ in range(1_000)
        ])
        codes = [r.status_code for r in results]
        assert all(c == 401 for c in codes), f"Non-401s: {[(i, c) for i, c in enumerate(codes) if c != 401][:5]}"

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_missing_content_type(self, client, live_endpoint):
        """Request without Content-Type header."""
        from tests.chaos.conftest import sign_payload
        payload = b'{"a": 1}'
        sig = sign_payload(live_endpoint["secret"], payload)
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers={
                "x-hub-signature-256": sig,
            },
        )
        assert resp.status_code == 202

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_unknown_headers_dont_break(self, client, live_endpoint):
        """Request with dozens of custom headers."""
        from tests.chaos.conftest import sign_payload
        payload = b'{"a": 1}'
        sig = sign_payload(live_endpoint["secret"], payload)
        headers = {"x-hub-signature-256": sig}
        for i in range(50):
            headers[f"x-custom-{i}"] = f"value-{i}" * 100
        resp = await client.post(
            f"/hooks/{live_endpoint['id']}",
            content=payload,
            headers=headers,
        )
        assert resp.status_code == 202
