"""Flood: fire N concurrent webhooks to an endpoint, then observe."""
import asyncio
import json
import hmac
import hashlib
import time
import httpx

ENDPOINT_ID = input("Endpoint ID: ").strip()
SECRET = "demo-secret-key-1234567890abcdef1234567890abcdef"
COUNT = 1000
CONCURRENT = 50

BASE = "http://localhost:8000"
SIGNED_URL = f"{BASE}/hooks/{ENDPOINT_ID}"


async def send(client, i):
    payload = {
        "event_type": "payment.succeeded",
        "amount": round(100 + i * 0.5, 2),
        "customer": {"name": f"User-{i}", "email": f"user{i}@example.com"},
        "timestamp": int(time.time()),
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    try:
        r = await client.post(
            SIGNED_URL,
            content=raw,
            headers={
                "x-hub-signature-256": f"sha256={sig}",
                "x-idempotency-key": f"flood-{i}-{int(time.time())}",
                "content-type": "application/json",
            },
            timeout=10,
        )
        return i, r.status_code
    except Exception as e:
        return i, str(e)


async def main():
    limits = httpx.Limits(max_connections=CONCURRENT)
    async with httpx.AsyncClient(limits=limits) as c:
        tasks = [send(c, i) for i in range(COUNT)]
        ok = fail = 0
        start = time.monotonic()
        for coro in asyncio.as_completed(tasks):
            idx, code = await coro
            if code == 202:
                ok += 1
            else:
                fail += 1
                print(f"  FAIL #{idx}: {code}")
        elapsed = time.monotonic() - start
    print(f"\nSent {ok} OK / {fail} failed in {elapsed:.1f}s ({COUNT/elapsed:.0f}/s)")


asyncio.run(main())
