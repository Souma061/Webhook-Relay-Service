"""
E2E test for Multi-Tenant User Management & RBAC.

Tests the full flow:
  1. Register user → auto-creates personal workspace
  2. Login → get JWT
  3. List workspaces → find personal workspace
  4. Create endpoint in workspace
  5. Add route to endpoint
  6. Send webhook → verify accepted
  7. List events (workspace-scoped)
  8. Test RBAC: viewer cannot create endpoint
  9. Test RBAC: member can create endpoint

Requires: RELAY_DATABASE_URL, RELAY_REDIS_URL pointing to running services.
"""

import os
import sys
import time

import httpx

BASE = os.environ.get("RELAY_BASE_URL", "http://localhost:8001")
import time
_uid = int(time.time())
EMAIL = f"e2e-{_uid}@test.com"
VIEWER_EMAIL = f"viewer-{_uid}@test.com"
PASSWORD = "testpassword123"

client = httpx.Client(base_url=BASE, timeout=15)


def ok(r, expected=200):
    assert r.status_code == expected, f"Expected {expected}, got {r.status_code}: {r.text}"
    return r


def step(num, desc):
    print(f"\n─── Step {num}: {desc} ───", flush=True)


# ── 1. Register ────────────────────────────────────────────────────────────────
step(1, "Register a new user")
r = ok(client.post("/api/auth/register", json={
    "email": EMAIL,
    "password": PASSWORD,
    "display_name": "E2E User",
}), 201)
token = r.json()["access_token"]
user_id = r.json()["user"]["id"]
print(f"  User registered: {user_id}")
print(f"  Token: {token[:40]}...")

headers = {"Authorization": f"Bearer {token}"}

# ── 2. Login (verify login works) ──────────────────────────────────────────────
step(2, "Login with credentials")
r = ok(client.post("/api/auth/login", json={
    "email": EMAIL,
    "password": PASSWORD,
}))
print(f"  Login OK — user: {r.json()['user']['display_name']}")

# ── 3. List workspaces ─────────────────────────────────────────────────────────
step(3, "List workspaces")
r = ok(client.get("/api/workspaces/", headers=headers))
workspaces = r.json()
assert len(workspaces) >= 1, "Expected at least 1 workspace"
ws = workspaces[0]
ws_id = ws["id"]
print(f"  Workspace: {ws['name']} (id={ws_id}, role={ws['role']})")

# ── 4. Create endpoint ─────────────────────────────────────────────────────────
step(4, "Create endpoint in workspace")
r = ok(client.post(f"/api/workspaces/{ws_id}/endpoints", headers=headers, json={
    "name": "E2E Test Endpoint",
}), 201)
ep = r.json()
ep_id = ep["id"]
print(f"  Endpoint created: {ep['name']} (id={ep_id})")
assert "hmac_secret" not in ep, "CRITICAL: hmac_secret should NOT be in EndpointOut!"
print(f"  ✅ hmac_secret NOT leaked in response")

# ── 5. Rotate secret (admin only) ──────────────────────────────────────────────
step(5, "Rotate HMAC secret")
r = ok(client.post(f"/api/workspaces/{ws_id}/endpoints/{ep_id}/rotate", headers=headers))
secret = r.json()["hmac_secret"]
assert len(secret) == 64, f"Expected 64-char hex secret, got {len(secret)} chars"
print(f"  Secret rotated: {secret[:16]}...")

# ── 6. Add route ───────────────────────────────────────────────────────────────
step(6, "Add delivery route")
r = ok(client.post(f"/api/workspaces/{ws_id}/endpoints/{ep_id}/routes", headers=headers, json={
    "name": "E2E Route",
    "url": "https://httpbin.org/post",
    "method": "POST",
    "filter_expression": "event == 'test'",
}), 201)
route = r.json()
route_id = route["id"]
print(f"  Route created: {route['name']} (id={route_id})")

# ── 7. List routes ─────────────────────────────────────────────────────────────
step(7, "List routes on endpoint")
r = ok(client.get(f"/api/workspaces/{ws_id}/endpoints/{ep_id}/routes", headers=headers))
routes = r.json()
assert len(routes) == 1
print(f"  Routes: {len(routes)} found")

# ── 8. Send webhook ────────────────────────────────────────────────────────────
step(8, "Send webhook to /hooks/{endpoint_id}")
import hmac, hashlib

payload = b'{"event": "test", "amount": 42}'
sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
r = ok(client.post(
    f"/hooks/{ep_id}",
    content=payload,
    headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "Idempotency-Key": "e2e-test-001",
        "X-GitHub-Event": "push",
    },
), 202)
event_id = r.json()["event_id"]
print(f"  Webhook accepted — event_id={event_id}")

# ── 9. List events (workspace-scoped) ──────────────────────────────────────────
step(9, "List events in workspace")
r = ok(client.get(f"/api/workspaces/{ws_id}/events", headers=headers))
events = r.json()
assert len(events) >= 1
print(f"  Events: {len(events)} found")

# ── 10. Get event detail ───────────────────────────────────────────────────────
step(10, "Get event detail")
r = ok(client.get(f"/api/workspaces/{ws_id}/events/{event_id}", headers=headers))
ev = r.json()
assert ev["request_body"]["event"] == "test"
print(f"  Event detail: id={ev['id']}, headers present={'request_headers' in ev}")

# ── 11. Get delivery attempts ──────────────────────────────────────────────────
step(11, "Get delivery attempts")
r = ok(client.get(f"/api/workspaces/{ws_id}/events/{event_id}/attempts", headers=headers))
attempts = r.json()
print(f"  Delivery attempts: {len(attempts)} (may be 0 if worker hasn't processed yet)")

# ── 12. Check duplicate idempotency ────────────────────────────────────────────
step(12, "Idempotency — resend with same key")
r = ok(client.post(
    f"/hooks/{ep_id}",
    content=payload,
    headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "Idempotency-Key": "e2e-test-001",
    },
), 202)
# Should return duplicate (not 202)
body = r.json()
print(f"  Duplicate response: {body}")
assert body["status"] == "duplicate", f"Expected duplicate, got {body}"

# ── 13. RBAC: Register a second user as viewer ────────────────────────────────
step(13, "RBAC — invite viewer, verify they cannot create endpoints")
r = ok(client.post("/api/auth/register", json={
    "email": VIEWER_EMAIL,
    "password": PASSWORD,
    "display_name": "Viewer User",
}), 201)
viewer_token = r.json()["access_token"]
viewer_id = r.json()["user"]["id"]
print(f"  Viewer user registered: {viewer_id}")

# Add viewer to workspace
r = ok(client.post(f"/api/workspaces/{ws_id}/members", headers=headers, json={
    "email": VIEWER_EMAIL,
    "role": "viewer",
}), 201)
print(f"  Viewer added to workspace")

# Viewer tries to create endpoint → should fail with 403
r = client.post(f"/api/workspaces/{ws_id}/endpoints", headers={"Authorization": f"Bearer {viewer_token}"}, json={
    "name": "Viewer's Endpoint",
})
assert r.status_code == 403, f"Expected 403 for viewer, got {r.status_code}: {r.text}"
print(f"  ✅ Viewer correctly denied (403) from creating endpoint")

# Viewer can list endpoints → should succeed
r = ok(client.get(f"/api/workspaces/{ws_id}/endpoints", headers={"Authorization": f"Bearer {viewer_token}"}))
print(f"  ✅ Viewer can list endpoints ({len(r.json())} found)")

# ── 14. RBAC: Promote viewer to member, verify they can create ─────────────────
step(14, "RBAC — promote viewer to member, verify they can create endpoint")
r = ok(client.put(f"/api/workspaces/{ws_id}/members/{viewer_id}", headers=headers, json={
    "role": "member",
}))
print(f"  Viewer promoted to member")

r = ok(client.post(f"/api/workspaces/{ws_id}/endpoints", headers={"Authorization": f"Bearer {viewer_token}"}, json={
    "name": "Member's Endpoint",
}), 201)
print(f"  ✅ Member can now create endpoints ({r.json()['name']})")

# ── 15. List members ───────────────────────────────────────────────────────────
step(15, "List workspace members")
r = ok(client.get(f"/api/workspaces/{ws_id}/members", headers=headers))
members = r.json()
print(f"  Members ({len(members)}):")
for m in members:
    print(f"    - {m['display_name']} ({m['email']}) — {m['role']}")

# ── 16. Get /me ────────────────────────────────────────────────────────────────
step(16, "GET /api/auth/me")
r = ok(client.get("/api/auth/me", headers=headers))
print(f"  Me: {r.json()['display_name']} ({r.json()['email']})")

# ── 17. Health check ───────────────────────────────────────────────────────────
step(17, "Health check")
r = ok(client.get("/health"))
print(f"  Health: {r.json()}")

print(f"\n{'='*60}")
print(f"✅ ALL {17} E2E TESTS PASSED")
print(f"{'='*60}")
