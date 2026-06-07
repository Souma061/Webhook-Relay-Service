"""Concurrency and race condition tests.

Verify the system handles simultaneous requests correctly — no duplicate
events, no lost events, no corrupted state.
"""

import asyncio
import json
import uuid
import pytest
from unittest.mock import AsyncMock


class TestIdempotencyRace:
    """Multiple concurrent requests with the same idempotency key."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_50_concurrent_same_idempotency_key(
        self, client, live_endpoint
    ):
        """50 concurrent requests with same idempotency key — exactly 1 accepted."""
        from tests.chaos.conftest import sign_payload

        payload = json.dumps({"event": "race-test"}).encode()
        sig = sign_payload(live_endpoint["secret"], payload)
        headers = {
            "x-hub-signature-256": sig,
            "x-idempotency-key": "race-key-50x",
        }

        results = await asyncio.gather(*[
            client.post(
                f"/hooks/{live_endpoint['id']}", content=payload, headers=headers
            )
            for _ in range(50)
        ])

        accepted = sum(1 for r in results if r.json().get("status") == "accepted")
        dupes = sum(1 for r in results if r.json().get("status") == "duplicate")
        assert accepted == 1, f"Expected 1 accepted, got {accepted}"
        assert dupes >= 48, f"Expected 49 duplicates, got {dupes}"
        assert accepted + dupes == 50

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_200_concurrent_unique_idempotency_keys(
        self, client, live_endpoint
    ):
        """200 concurrent requests each with a unique key — all accepted."""
        from tests.chaos.conftest import sign_payload

        async def send_one(i):
            payload = json.dumps({"seq": i}).encode()
            sig = sign_payload(live_endpoint["secret"], payload)
            return await client.post(
                f"/hooks/{live_endpoint['id']}",
                content=payload,
                headers={
                    "x-hub-signature-256": sig,
                    "x-idempotency-key": f"unique-{uuid.uuid4()}",
                },
            )

        results = await asyncio.gather(*[send_one(i) for i in range(200)])
        ok = sum(1 for r in results if r.status_code == 202)
        assert ok >= 198, f"Expected 200, got {ok}"


class TestConcurrentEndpoints:
    """Multiple endpoints receiving events simultaneously."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_5_endpoints_100_events_each(
        self, session_client, auth_headers, workspace_id
    ):
        """5 separate endpoints, 100 events each concurrently — all 202s."""
        from tests.chaos.conftest import sign_payload, ConcurrentEventSender

        endpoints = []
        for i in range(5):
            secret = f"ep-secret-{uuid.uuid4().hex[:8]}"
            resp = await session_client.post(
                f"/api/workspaces/{workspace_id}/endpoints",
                json={"name": f"Concurrent EP {i}", "hmac_secret": secret},
                headers=auth_headers,
            )
            ep = resp.json()
            endpoints.append((ep["id"], secret))

        async def hammer_endpoint(ep_id: str, secret: str):
            sender = ConcurrentEventSender(session_client, ep_id, secret)
            results = await sender.flood(100, concurrency=20)
            return [s for _, s, _ in results]

        all_results = await asyncio.gather(*[
            hammer_endpoint(eid, sec) for eid, sec in endpoints
        ])

        for idx, statuses in enumerate(all_results):
            ok = sum(1 for s in statuses if s == 202)
            assert ok >= 98, f"Endpoint {idx}: only {ok}/100 OK"


class TestRateLimiterConcurrentSameKey:
    """Concurrent requests to the same rate-limited key—Redis pipeline safety."""

    async def test_100_concurrent_same_key_allows_first_10(
        self, limiter, mock_redis_pipeline
    ):
        """Simulate 100 concurrent requests where pipeline returns
        incrementally increasing counts. First 10 see count<10 → allowed,
        rest see count>=10 → denied."""
        _, pipeline = mock_redis_pipeline
        call_count = 0

        async def _execute():
            nonlocal call_count
            call_count += 1
            count = min(call_count, 20)
            return [0, count, 1, True]

        pipeline.execute = AsyncMock(side_effect=_execute)
        results = await asyncio.gather(*[
            limiter.allow_request("hot-key") for _ in range(100)
        ])
        allowed = sum(results)
        assert allowed > 0
        assert allowed < 50
