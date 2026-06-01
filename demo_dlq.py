import hmac
import hashlib
import urllib.request
import urllib.error
import json
import time
import sys

def request(url, method="GET", headers=None, data=None):
    if headers is None:
        headers = {}
    
    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode('utf-8')
        headers["Content-Type"] = "application/json"
        
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return res.getcode(), json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body
    except Exception as e:
        return 0, str(e)

def run_demo():
    print("=============================================================")
    print("             DEMO 2: DEAD LETTER QUEUE (DLQ)                 ")
    print("=============================================================")
    print("This script will create a route that is guaranteed to fail,  ")
    print("exhausting all retries so it lands in the Dead Letter Queue.")
    print("=============================================================\n")

    # 1. Create Endpoint
    print("1. Creating ingestion endpoint in database...")
    status, ep = request("http://localhost:8000/api/endpoints/", method="POST", data={"name": "DLQ Simulation Endpoint"})
    if status != 201:
        print(f"Failed to create endpoint: {status} {ep}")
        return
    
    ep_id = ep["id"]
    secret = ep["hmac_secret"].encode('utf-8')
    print(f"   -> Created Endpoint ID: {ep_id}")
    print(f"   -> HMAC Secret: {ep['hmac_secret']}")
    
    # 2. Register failing route (max_retries = 3)
    print("\n2. Creating failing route (pointing to non-existent port 54321)...")
    route_data = {
        "name": "Failing Destination",
        "url": "http://localhost:54321/incoming-webhook",
        "method": "POST",
        "timeout_ms": 1000,
        "max_retries": 3,
        "retry_backoff_ms": 500
    }
    status, route = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_data)
    if status != 201:
        print(f"Failed to create route: {status} {route}")
        return
    print(f"   -> Created Route ID: {route['id']} pointing to {route['url']}")

    # 3. Ingest signed payload
    print("\n3. Ingesting test payload at gateway...")
    payload = {
        "event_type": "payment.failed",
        "error_code": "card_declined",
        "simulation": "dead-letter-queue",
        "timestamp": int(time.time())
    }
    raw_body = json.dumps(payload).encode('utf-8')
    sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    
    status, res = request(
        f"http://localhost:8000/hooks/{ep_id}",
        method="POST",
        headers={
            "x-hub-signature-256": f"sha256={sig}",
            "idempotency-key": f"demo-dlq-{int(time.time())}"
        },
        data=payload
    )
    if status != 202:
        print(f"Failed to ingest: {status} {res}")
        return
    print(f"   -> Gateway Response: {res} (HTTP {status})")

    # 4. Wait for retry exhaustion
    print("\n4. Waiting for delivery attempts and backoffs to exhaust...")
    for i in range(12):
        time.sleep(1)
        sys.stdout.write(f"\r      Waiting... {i+1}s / 12s")
        sys.stdout.flush()
    print("\n")

    # 5. Success message
    print("=============================================================")
    print("                       SUCCESS                               ")
    print("=============================================================")
    print("All delivery retries have been exhausted successfully!")
    print("Open http://localhost:5173/dlq and click 'Refresh' to view it.")
    print("You can try replaying or discarding it from the dashboard!")
    print("=============================================================")

if __name__ == "__main__":
    run_demo()
