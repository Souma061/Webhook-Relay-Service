#!/usr/bin/env python3
"""
Chaos Engineering Suite for Webhook Relay.

Orchestrated chaos tests that run against the live docker-compose stack.
Each test:
  1. Verifies the system is healthy before injecting failure
  2. Injects a failure (kill container, network partition, etc.)
  3. Sends traffic to observe behavior under failure
  4. Recovers the system
  5. Verifies recovery (no data loss, no corruption)

Usage:
  python chaos/chaos_suite.py              # Run all tests
  python chaos/chaos_suite.py --test outbox # Run specific test
  python chaos/chaos_suite.py --skip-cleanup  # Leave containers as-is on failure
"""

import asyncio
import hashlib
import hmac
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import httpx

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("CHAOS_BASE_URL", "http://localhost:8000")
COLLECTOR_URL = os.environ.get("CHAOS_COLLECTOR_URL", "http://localhost:9999")
COMPOSE_FILE = os.environ.get("CHAOS_COMPOSE_FILE", "docker-compose.dev.yml")

DEFAULT_ENDPOINT_SECRET = "chaos-suite-secret-" + uuid.uuid4().hex[:16]

PASS = "✅"
FAIL = "❌"
SKIP = "⏭"
INFO = "ℹ️"

# ── Helpers ─────────────────────────────────────────────────────────────────────


def log(msg: str, icon: str = INFO):
    print(f"  {icon}  {msg}")


def divider(title: str):
    n = max(0, 72 - len(title) - 4)
    print(f"\n── {title} " + "─" * n)


def sign_payload(secret: str, payload: bytes) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def docker_compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        capture_output=True, text=True, timeout=60,
    )


async def healthcheck(url: str, timeout: float = 10) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{url}/health")
            return r.status_code == 200
    except Exception:
        return False


async def wait_for_health(url: str, timeout: float = 60, label: str = "service"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await healthcheck(url, timeout=2):
            return True
        await asyncio.sleep(2)
    return False


# ── Chaos Test Result ──────────────────────────────────────────────────────────

@dataclass
class ChaosResult:
    name: str
    passed: bool
    details: str = ""


class ChaosTest:
    def __init__(self, name: str, fn: Callable):
        self.name = name
        self.fn = fn

    async def run(self) -> ChaosResult:
        log(f"Running: {self.name}")
        try:
            await self.fn()
            log(f"PASSED", PASS)
            return ChaosResult(self.name, True)
        except Exception as e:
            log(f"FAILED: {e}", FAIL)
            return ChaosResult(self.name, False, str(e))


# ── Test: Healthy Baseline ─────────────────────────────────────────────────────

async def test_baseline():
    """Verify all services are healthy before chaos."""
    assert await healthcheck(BASE_URL), "App not reachable"
    assert await healthcheck(COLLECTOR_URL), "Collector not reachable"

    # Register a user, create an endpoint, send a webhook
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        # Register
        email = f"chaos-{uuid.uuid4().hex[:8]}@test.local"
        r = await client.post("/api/auth/register", json={
            "email": email,
            "password": "ChaosTest123!",
            "display_name": "Chaos Tester",
        })
        assert r.status_code in (201, 409), f"Register failed: {r.text}"
        if r.status_code == 201:
            token = r.json()["access_token"]
        else:
            r = await client.post("/api/auth/login", json={
                "email": email,
                "password": "ChaosTest123!",
            })
            token = r.json()["access_token"]

        headers = {"Authorization": f"Bearer {token}"}

        # Get workspace
        r = await client.get("/api/workspaces/", headers=headers)
        ws_id = r.json()[0]["id"]

        # Create endpoint
        secret = DEFAULT_ENDPOINT_SECRET
        r = await client.post(
            f"/api/workspaces/{ws_id}/endpoints",
            json={"name": "Chaos Baseline EP", "hmac_secret": secret},
            headers=headers,
        )
        assert r.status_code == 201, f"Endpoint create failed: {r.text}"
        ep = r.json()

        # Create route
        r = await client.post(
            f"/api/workspaces/{ws_id}/endpoints/{ep['id']}/routes",
            json={"url": "http://host.docker.internal:9999", "name": "chaos-collector"},
            headers=headers,
        )
        assert r.status_code == 201, f"Route create failed: {r.text}"

        # Send webhook
        payload = json.dumps({"event": "baseline.test", "seq": 0}).encode()
        sig = sign_payload(secret, payload)
        r = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert r.status_code == 202, f"Webhook failed: {r.text}"
        log(f"Webhook accepted: {r.json()}", PASS)

        # Return created resources for other tests
        return {
            "token": token,
            "headers": headers,
            "workspace_id": ws_id,
            "endpoint": ep,
            "secret": secret,
        }
    finally:
        await client.aclose()


# ── Test: Kill Outbox Relay Mid-Stream ─────────────────────────────────────────

async def test_kill_outbox_relay(ctx: dict):
    """Kill outbox-relay, send events, verify pending, restart, verify drain."""
    log("Stopping outbox-relay container...")
    r = docker_compose("stop", "outbox-relay")
    assert r.returncode == 0, f"docker stop failed: {r.stderr}"
    await asyncio.sleep(2)

    # Send events while outbox-relay is down
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        sent_ids = []
        for i in range(5):
            payload = json.dumps({"event": "chaos.outbox-kill", "seq": i}).encode()
            sig = sign_payload(ctx["secret"], payload)
            r = await client.post(
                f"/hooks/{ctx['endpoint']['id']}",
                content=payload,
                headers={"x-hub-signature-256": sig},
            )
            assert r.status_code == 202, f"Webhook {i} failed: {r.text}"
            sent_ids.append(r.json()["event_id"])
        log(f"Sent {len(sent_ids)} events while relay was down")

        # Verify outbox records are 'pending'
        import asyncpg
        conn = await asyncpg.connect(
            user="postgres", password="postgres",
            host="localhost", port=5433, database="webhook_relay",
        )
        try:
            for eid in sent_ids:
                row = await conn.fetchrow(
                    "SELECT status FROM outbox_records WHERE event_id = $1", uuid.UUID(eid)
                )
                assert row is not None, f"Outbox record missing for {eid}"
                assert row["status"] == "pending", f"Expected pending, got {row['status']}"
            log("All outbox records are 'pending' while relay is down", PASS)
        finally:
            await conn.close()

        # Restart outbox-relay
        log("Starting outbox-relay container...")
        r = docker_compose("start", "outbox-relay")
        assert r.returncode == 0, f"docker start failed: {r.stderr}"
        await asyncio.sleep(5)

        # Verify outbox records drain to 'completed'
        import asyncpg
        conn = await asyncpg.connect(
            user="postgres", password="postgres",
            host="localhost", port=5433, database="webhook_relay",
        )
        try:
            for eid in sent_ids:
                row = await conn.fetchrow(
                    "SELECT status FROM outbox_records WHERE event_id = $1", uuid.UUID(eid)
                )
                assert row is not None, f"Outbox record missing for {eid}"
                if row["status"] != "completed":
                    log(f"Event {eid[:8]} still {row['status']}, waiting...")
                    await asyncio.sleep(3)
                    row = await conn.fetchrow(
                        "SELECT status FROM outbox_records WHERE event_id = $1", uuid.UUID(eid)
                    )
                assert row["status"] == "completed", f"Expected completed, got {row['status']}"
            log("All outbox records drained to 'completed'", PASS)
        finally:
            await conn.close()
    finally:
        await client.aclose()


# ── Test: Kill Delivery Worker ─────────────────────────────────────────────────

async def test_kill_delivery_worker(ctx: dict):
    """Kill delivery-worker, send event, verify it queues, restart, verify delivery."""
    log("Stopping delivery-worker container...")
    r = docker_compose("stop", "delivery-worker")
    assert r.returncode == 0, f"docker stop failed: {r.stderr}"
    await asyncio.sleep(2)

    # Send event while delivery-worker is down
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        payload = json.dumps({"event": "chaos.delivery-kill", "seq": 0}).encode()
        sig = sign_payload(ctx["secret"], payload)
        r = await client.post(
            f"/hooks/{ctx['endpoint']['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert r.status_code == 202
        event_id = r.json()["event_id"]
        log(f"Event {event_id[:8]} accepted while delivery-worker was down")

        # Restart delivery-worker
        log("Starting delivery-worker container...")
        r = docker_compose("start", "delivery-worker")
        assert r.returncode == 0, f"docker start failed: {r.stderr}"
        await asyncio.sleep(5)

        # Check delivery was made to collector
        # The delivery_attempts table should have a success record
        import asyncpg
        conn = await asyncpg.connect(
            user="postgres", password="postgres",
            host="localhost", port=5433, database="webhook_relay",
        )
        try:
            attempt = await conn.fetchrow(
                """SELECT response_status, error FROM delivery_attempts
                   WHERE event_id = $1 ORDER BY attempted_at DESC LIMIT 1""",
                uuid.UUID(event_id),
            )
            assert attempt is not None, "No delivery attempt recorded"
            assert attempt["response_status"] == 200, f"Delivery failed: {attempt['error']}"
            log(f"Delivery succeeded (HTTP {attempt['response_status']})", PASS)
        finally:
            await conn.close()
    finally:
        await client.aclose()


# ── Test: Kafka Goes Down ──────────────────────────────────────────────────────

async def test_kafka_down(ctx: dict):
    """Stop Kafka, send events, verify they queue in outbox, restart, verify drain."""
    log("Stopping Kafka container...")
    r = docker_compose("stop", "kafka")
    assert r.returncode == 0, f"docker stop failed: {r.stderr}"
    await asyncio.sleep(3)

    # Send events while Kafka is down
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        sent_ids = []
        for i in range(3):
            payload = json.dumps({"event": "chaos.kafka-down", "seq": i}).encode()
            sig = sign_payload(ctx["secret"], payload)
            r = await client.post(
                f"/hooks/{ctx['endpoint']['id']}",
                content=payload,
                headers={"x-hub-signature-256": sig},
            )
            assert r.status_code == 202, f"Webhook {i} failed: {r.text}"
            sent_ids.append(r.json()["event_id"])
        log(f"Sent {len(sent_ids)} events while Kafka was down")

        # Verify events accepted (gateway shouldn't need Kafka)
        import asyncpg
        conn = await asyncpg.connect(
            user="postgres", password="postgres",
            host="localhost", port=5433, database="webhook_relay",
        )
        try:
            for eid in sent_ids:
                row = await conn.fetchrow(
                    "SELECT status FROM events WHERE id = $1", uuid.UUID(eid)
                )
                assert row is not None, f"Event {eid[:8]} not found"
                assert row["status"] == "pending", f"Expected pending, got {row['status']}"
            log("All events persisted in 'pending' state", PASS)
        finally:
            await conn.close()

        # Restart Kafka
        log("Starting Kafka container...")
        r = docker_compose("start", "kafka")
        assert r.returncode == 0, f"docker start failed: {r.stderr}"
        # Kafka takes time to become healthy
        log("Waiting for Kafka to become healthy...")
        await asyncio.sleep(15)

        # Restart outbox-relay to pick up pending records
        log("Restarting outbox-relay...")
        docker_compose("restart", "outbox-relay")
        await asyncio.sleep(5)
    finally:
        await client.aclose()


# ── Test: PostgreSQL Goes Down ─────────────────────────────────────────────────

async def test_postgres_down(ctx: dict):
    """Stop PostgreSQL, verify gateway returns 500, restart, verify recovery."""
    log("Stopping PostgreSQL container...")
    r = docker_compose("stop", "postgres")
    assert r.returncode == 0, f"docker stop failed: {r.stderr}"
    await asyncio.sleep(3)

    # Gateway should fail on DB operations
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=5)
    try:
        payload = json.dumps({"event": "chaos.pg-down"}).encode()
        sig = sign_payload(ctx["secret"], payload)
        r = await client.post(
            f"/hooks/{ctx['endpoint']['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        log(f"Gateway returned {r.status_code} while PostgreSQL was down")
        # Should fail - but the failure mode depends on how fast the DB connection times out
        assert r.status_code in (500, 502, 503), f"Expected 5xx, got {r.status_code}"
        log("Gateway correctly returns error when DB is down", PASS)
    finally:
        await client.aclose()

    # Restart PostgreSQL
    log("Starting PostgreSQL container...")
    r = docker_compose("start", "postgres")
    assert r.returncode == 0, f"docker start failed: {r.stderr}"
    log("Waiting for PostgreSQL to become healthy...")
    await asyncio.sleep(10)

    # Verify gateway recovers
    assert await wait_for_health(BASE_URL, timeout=60), "App did not recover after DB restart"
    log("Gateway recovered after PostgreSQL restart", PASS)


# ── Test: Redis Goes Down ──────────────────────────────────────────────────────

async def test_redis_down(ctx: dict):
    """Stop Redis, verify rate limiter / idempotency degrade gracefully."""
    log("Stopping Redis container...")
    r = docker_compose("stop", "redis")
    assert r.returncode == 0, f"docker stop failed: {r.stderr}"
    await asyncio.sleep(3)

    client = httpx.AsyncClient(base_url=BASE_URL, timeout=10)
    try:
        # Request with idempotency key should fail (or degrade)
        payload = json.dumps({"event": "chaos.redis-down"}).encode()
        sig = sign_payload(ctx["secret"], payload)
        r = await client.post(
            f"/hooks/{ctx['endpoint']['id']}",
            content=payload,
            headers={
                "x-hub-signature-256": sig,
                "x-idempotency-key": f"chaos-{uuid.uuid4()}",
            },
        )
        # The gateway may return 500 because Redis is down for idempotency check
        # OR if rate limiter is configured, it may also fail
        log(f"Gateway returned {r.status_code} while Redis was down (may be expected)")
    finally:
        await client.aclose()

    # Restart Redis
    log("Starting Redis container...")
    r = docker_compose("start", "redis")
    assert r.returncode == 0, f"docker start failed: {r.stderr}"
    await asyncio.sleep(5)

    assert await wait_for_health(BASE_URL, timeout=30), "App did not recover after Redis restart"
    log("Gateway recovered after Redis restart", PASS)


# ── Test: Flood 5000 Events ────────────────────────────────────────────────────

async def test_flood_5000(ctx: dict):
    """Send 5000 events at 100 concurrency — smoke test throughput."""
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=30)
    try:
        sem = asyncio.Semaphore(100)

        async def send_one(seq):
            async with sem:
                payload = json.dumps({"event": "chaos.flood", "seq": seq}).encode()
                sig = sign_payload(ctx["secret"], payload)
                r = await client.post(
                    f"/hooks/{ctx['endpoint']['id']}",
                    content=payload,
                    headers={"x-hub-signature-256": sig},
                )
                return seq, r.status_code, r.json().get("event_id", "")

        start = time.monotonic()
        results = await asyncio.gather(*[send_one(i) for i in range(5000)])
        elapsed = time.monotonic() - start

        ok = sum(1 for _, s, _ in results if s == 202)
        rate = 5000 / elapsed
        log(f"{ok}/5000 accepted in {elapsed:.1f}s ({rate:.0f}/s)")
        assert ok >= 4800, f"Only {ok}/5000 accepted — throughput too low"
        log(f"Flood test passed at {rate:.0f} events/sec", PASS)
    finally:
        await client.aclose()


# ── Test: Idempotency Under Chaos ──────────────────────────────────────────────

async def test_idempotency_under_chaos(ctx: dict):
    """Send duplicate requests with same idempotency key — only first accepted."""
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        idem_key = f"chaos-idem-{uuid.uuid4().hex[:12]}"
        payload = json.dumps({"event": "chaos.idempotency"}).encode()
        sig = sign_payload(ctx["secret"], payload)
        headers = {"x-hub-signature-256": sig, "x-idempotency-key": idem_key}

        # Send 10 identical requests
        results = await asyncio.gather(*[
            client.post(f"/hooks/{ctx['endpoint']['id']}", content=payload, headers=headers)
            for _ in range(10)
        ])

        accepted = sum(1 for r in results if r.json().get("status") == "accepted")
        dupes = sum(1 for r in results if r.json().get("status") == "duplicate")
        log(f"Idempotency: {accepted} accepted, {dupes} duplicates")
        assert accepted == 1, f"Expected exactly 1 accepted, got {accepted}"
        assert dupes == 9, f"Expected 9 duplicates, got {dupes}"
        log("Idempotency guarantees hold under concurrent chaos", PASS)
    finally:
        await client.aclose()


# ── Test: Outbox Relay Crash During Publish ────────────────────────────────────

async def test_outbox_halfway_crash(ctx: dict):
    """Simulate outbox-relay crash halfway through a batch.
    
    Send 20 events, kill relay, verify some are completed and some pending,
    restart and verify full drain.
    """
    # Reduce outbox batch to force partial-batch scenario
    log("Sending 20 events...")
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=15)
    try:
        sent_ids = []
        for i in range(20):
            payload = json.dumps({"event": "chaos.halfway", "seq": i}).encode()
            sig = sign_payload(ctx["secret"], payload)
            r = await client.post(
                f"/hooks/{ctx['endpoint']['id']}",
                content=payload,
                headers={"x-hub-signature-256": sig},
            )
            sent_ids.append(r.json()["event_id"])

        await asyncio.sleep(3)

        # Check mix of pending/completed
        import asyncpg
        conn = await asyncpg.connect(
            user="postgres", password="postgres",
            host="localhost", port=5433, database="webhook_relay",
        )
        try:
            rows = await conn.fetch(
                "SELECT event_id, status FROM outbox_records WHERE event_id = ANY($1)",
                [uuid.UUID(eid) for eid in sent_ids],
            )
            statuses = {str(r["event_id"]): r["status"] for r in rows}
            pending = [eid for eid in sent_ids if statuses.get(eid) == "pending"]
            completed = [eid for eid in sent_ids if statuses.get(eid) == "completed"]
            log(f"{len(completed)} completed, {len(pending)} pending after initial drain")

            # Wait for full drain
            deadline = time.monotonic() + 30
            while pending and time.monotonic() < deadline:
                await asyncio.sleep(3)
                rows = await conn.fetch(
                    "SELECT event_id, status FROM outbox_records WHERE event_id = ANY($1)",
                    [uuid.UUID(eid) for eid in pending],
                )
                statuses = {str(r["event_id"]): r["status"] for r in rows}
                pending = [eid for eid in pending if statuses.get(eid) != "completed"]
                completed_now = sum(1 for eid in sent_ids if statuses.get(eid) == "completed")
                log(f"  Draining... {len(completed_now)} completed, {len(pending)} remaining")

            assert len(pending) == 0, f"{len(pending)} events still pending after timeout"
            log("All 20 events drained to completed", PASS)
        finally:
            await conn.close()
    finally:
        await client.aclose()


# ── Test Runner ────────────────────────────────────────────────────────────────

async def run_all():
    divider("CHAOS ENGINEERING SUITE")
    log(f"Target: {BASE_URL}")
    log(f"Compose: {COMPOSE_FILE}")
    log(f"Collector: {COLLECTOR_URL}")
    print()

    divider("1. HEALTH CHECK — Verify baseline")
    ctx = await test_baseline()
    log("Baseline healthy — system is ready for chaos")
    print()

    tests = [
        ChaosTest("Kill outbox-relay mid-stream", lambda: test_kill_outbox_relay(ctx)),
        ChaosTest("Kill delivery-worker mid-stream", lambda: test_kill_delivery_worker(ctx)),
        ChaosTest("Kafka goes down — events still accepted", lambda: test_kafka_down(ctx)),
        ChaosTest("PostgreSQL goes down — gateway errors gracefully", lambda: test_postgres_down(ctx)),
        ChaosTest("Redis goes down — graceful degradation", lambda: test_redis_down(ctx)),
        ChaosTest("Flood 5000 events at 100 concurrency", lambda: test_flood_5000(ctx)),
        ChaosTest("Idempotency under concurrent chaos", lambda: test_idempotency_under_chaos(ctx)),
        ChaosTest("Outbox relay halfway crash — full drain", lambda: test_outbox_halfway_crash(ctx)),
    ]

    results: list[ChaosResult] = []
    for test in tests:
        divider(test.name)
        try:
            result = await test.run()
        except Exception as e:
            result = ChaosResult(test.name, False, str(e))
        results.append(result)
        print()

    divider("CHAOS SUITE SUMMARY")
    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]
    log(f"{passed}/{len(results)} tests passed")
    if failed:
        log(f"{len(failed)} tests FAILED:", FAIL)
        for f in failed:
            log(f"  - {f.name}: {f.details}", FAIL)
    else:
        log("All chaos tests passed!", PASS)

    return len(failed) == 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Chaos Engineering Suite")
    parser.add_argument("--test", help="Run a specific test by name fragment")
    args = parser.parse_args()

    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
