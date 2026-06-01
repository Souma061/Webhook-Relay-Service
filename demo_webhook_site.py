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
    except Exception as e:
        return 0, str(e)

def run_demo():
    print("=============================================================")
    print("        DEMO 1: WEBHOOK.SITE REAL-TIME DELIVERY              ")
    print("=============================================================")
    print("This script will create a route targeting your Webhook.site   ")
    print("and deliver a webhook payload in real-time.")
    print("=============================================================\n")

    # Ask the user for their webhook.site URL
    webhook_url = input("Please paste your unique Webhook.site URL: ").strip()
    if not webhook_url.startswith("http"):
        print("Invalid URL format. Please start with http:// or https://")
        return

    # 1. Create Endpoint
    print("\n1. Creating ingestion endpoint in database...")
    status, ep = request("http://localhost:8000/api/endpoints/", method="POST", data={"name": "Webhook.site Ingestion Endpoint"})
    if status != 201:
        print(f"Failed to create endpoint: {status} {ep}")
        return
    
    ep_id = ep["id"]
    secret = ep["hmac_secret"].encode('utf-8')
    print(f"   -> Created Endpoint ID: {ep_id}")
    print(f"   -> HMAC Secret: {ep['hmac_secret']}")
    
    # 2. Register route
    print("\n2. Creating delivery route pointing to Webhook.site...")
    route_data = {
        "name": "Webhook.site Destination",
        "url": webhook_url,
        "method": "POST",
        "timeout_ms": 5000,
        "max_retries": 1
    }
    status, route = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_data)
    if status != 201:
        print(f"Failed to create route: {status} {route}")
        return
    print(f"   -> Created Route ID: {route['id']} pointing to {webhook_url}")

    # 3. Ingest signed payload
    print("\n3. Ingesting test payload at gateway...")
    payload = {
        "event_type": "payment.succeeded",
        "amount": 299.99,
        "customer": {
            "name": "John Doe",
            "email": "john@example.com"
        },
        "timestamp": int(time.time())
    }
    raw_body = json.dumps(payload).encode('utf-8')
    sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    
    status, res = request(
        f"http://localhost:8000/hooks/{ep_id}",
        method="POST",
        headers={
            "x-hub-signature-256": f"sha256={sig}",
            "idempotency-key": f"demo-ws-{int(time.time())}"
        },
        data=payload
    )
    if status != 202:
        print(f"Failed to ingest: {status} {res}")
        return
    print(f"   -> Gateway Response: {res} (HTTP {status})")
    
    # 4. Success message
    print("\n=============================================================")
    print("                       SUCCESS                               ")
    print("=============================================================")
    print("The webhook ingestion task is successfully completed!")
    print("Check your browser window at Webhook.site to see the payload.")
    print("Check http://localhost:5173/events to view it in the app logs.")
    print("=============================================================")

if __name__ == "__main__":
    run_demo()
