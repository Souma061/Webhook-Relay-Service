import hmac
import hashlib
import urllib.request
import urllib.error
import json
import time

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

def run_simulation():
    print("=== Webhook Relay DLQ Event Generator ===")
    
    # 1. Create a persistent Endpoint for the simulation
    print("\n1. Creating simulation endpoint...")
    status, ep = request("http://localhost:8000/api/endpoints/", method="POST", data={"name": "DLQ Simulation Endpoint"})
    if status != 201:
        print(f"Failed to create endpoint: {status} {ep}")
        return
    
    ep_id = ep["id"]
    secret = ep["hmac_secret"].encode('utf-8')
    print(f"Created Endpoint ID: {ep_id}")
    print(f"HMAC Secret: {ep['hmac_secret']}")
    
    # 2. Create a delivery route that is guaranteed to fail
    print("\n2. Creating failing route...")
    route_data = {
        "name": "Failing Local Endpoint",
        "url": "http://localhost:54321/incoming-webhook",
        "method": "POST",
        "timeout_ms": 1000,
        "max_retries": 3
    }
    status, route = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_data)
    if status != 201:
        print(f"Failed to create route: {status} {route}")
        return
        
    print(f"Created Route ID: {route['id']} pointing to {route['url']}")
    
    # 3. Send the webhook request
    print("\n3. Ingesting test webhook payload...")
    payload = {
        "event": "order.completed",
        "order_id": "ord_998877",
        "amount_usd": 150.00,
        "customer": {
            "name": "Alice Smith",
            "email": "alice@example.com"
        },
        "simulation": "dead-letter-queue",
        "generated_at": int(time.time())
    }
    raw_body = json.dumps(payload).encode('utf-8')
    signature = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    
    gateway_url = f"http://localhost:8000/hooks/{ep_id}"
    req = urllib.request.Request(
        gateway_url,
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "x-hub-signature-256": f"sha256={signature}",
            "idempotency-key": f"dlq-event-{int(time.time())}"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as res:
            res_status = res.getcode()
            res_text = res.read().decode('utf-8')
            print(f"Gateway Response: {res_text} (HTTP {res_status})")
    except Exception as e:
        print(f"Error sending request to gateway: {e}")
        return

    # 4. Wait for delivery worker to attempt delivery and exhaust retries
    # 3 attempts with exponential backoff should take around 6-8 seconds
    print("\n4. Waiting for delivery attempts to fail and exhaust retries...")
    for i in range(12):
        time.sleep(1)
        print(f"Waiting... {i+1}s / 12s")
        
    print("\n=== Simulation Complete ===")
    print("A failed delivery has been generated and retries have been exhausted.")
    print("Go to http://localhost:5173/dlq and click 'Refresh' to see it in your browser!")
    print("You can try replaying, discarding, or restoring the event directly from the dashboard.")

if __name__ == "__main__":
    run_simulation()
