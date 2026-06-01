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
    print("          WEBHOOK RELAY E2E DEMONSTRATION SCRIPT             ")
    print("=============================================================")
    print("This script will demonstrate Dynamic Route Filtering (Phase 3)")
    print("and Dead Letter Queue (DLQ) processing in one single run.")
    print("=============================================================\n")

    # 1. Create a persistent Endpoint for the demonstration
    print("1. Creating demonstration endpoint...")
    status, ep = request("http://localhost:8000/api/endpoints/", method="POST", data={"name": "E2E Video Demo Endpoint"})
    if status != 201:
        print(f"Failed to create endpoint: {status} {ep}")
        return
    
    ep_id = ep["id"]
    secret = ep["hmac_secret"].encode('utf-8')
    print(f"   -> Created Endpoint ID: {ep_id}")
    print(f"   -> HMAC Secret: {ep['hmac_secret']}\n")
    
    # 2. Register three routes with different filter rules
    print("2. Registering destination routes...")
    
    # Route 1: No filter (matches all events)
    route_all_data = {
        "name": "Route A (No Filter)",
        "url": "http://localhost:54321/always-deliver",
        "method": "POST",
        "timeout_ms": 1000,
        "max_retries": 1
    }
    status, route_all = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_all_data)
    print(f"   -> Created Route A: '{route_all['name']}' (Filter: None)")

    # Route 2: Success filter (matches payment.succeeded)
    route_success_data = {
        "name": "Route B (Success Only)",
        "url": "http://localhost:54321/success-only",
        "method": "POST",
        "timeout_ms": 1000,
        "max_retries": 1,
        "filter_expression": "event_type == 'payment.succeeded'"
    }
    status, route_success = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_success_data)
    print(f"   -> Created Route B: '{route_success['name']}' (Filter: {route_success['filter_expression']})")

    # Route 3: Failed filter (matches payment.failed, will exhaust retries to show DLQ)
    route_failed_data = {
        "name": "Route C (Failing Destination - DLQ)",
        "url": "http://localhost:54321/failed-only",
        "method": "POST",
        "timeout_ms": 1000,
        "max_retries": 3,
        "filter_expression": "event_type == 'payment.failed'"
    }
    status, route_failed = request(f"http://localhost:8000/api/endpoints/{ep_id}/routes", method="POST", data=route_failed_data)
    print(f"   -> Created Route C: '{route_failed['name']}' (Filter: {route_failed['filter_expression']})")
    print()

    # 3. Dispatched event A (payment.succeeded)
    print("3. Ingesting 'payment.succeeded' webhook event...")
    payload_a = {"event_type": "payment.succeeded", "amount": 120.50}
    raw_body_a = json.dumps(payload_a).encode('utf-8')
    sig_a = hmac.new(secret, raw_body_a, hashlib.sha256).hexdigest()
    
    status, res_a = request(
        f"http://localhost:8000/hooks/{ep_id}",
        method="POST",
        headers={"x-hub-signature-256": f"sha256={sig_a}"},
        data=payload_a
    )
    event_id_a = res_a["event_id"]
    print(f"   -> Webhook A Ingested: Event ID = {event_id_a}")
    print("   -> Waiting 3 seconds for worker processing...")
    time.sleep(3)

    # Query attempts for event A
    status, attempts_a = request(f"http://localhost:8000/api/events/{event_id_a}/attempts")
    print("\n   [Filter evaluation check for payment.succeeded]:")
    attempted_route_ids = [att["route_id"] for att in attempts_a]
    
    print(f"   - Route A (No Filter): {'Attempted (matching always)' if route_all['id'] in attempted_route_ids else 'Skipped'}")
    print(f"   - Route B (Success Filter): {'Attempted (MATCH)' if route_success['id'] in attempted_route_ids else 'Skipped'}")
    print(f"   - Route C (Failed Filter): {'Attempted' if route_failed['id'] in attempted_route_ids else 'SKIPPED (Filter mismatch!)'}")
    print()

    # 4. Dispatched event B (payment.failed)
    print("4. Ingesting 'payment.failed' webhook event...")
    payload_b = {"event_type": "payment.failed", "error_code": "card_declined"}
    raw_body_b = json.dumps(payload_b).encode('utf-8')
    sig_b = hmac.new(secret, raw_body_b, hashlib.sha256).hexdigest()
    
    status, res_b = request(
        f"http://localhost:8000/hooks/{ep_id}",
        method="POST",
        headers={"x-hub-signature-256": f"sha256={sig_b}"},
        data=payload_b
    )
    event_id_b = res_b["event_id"]
    print(f"   -> Webhook B Ingested: Event ID = {event_id_b}")
    print("   -> Waiting 12 seconds for delivery attempts & exponential backoffs to exhaust...")
    for i in range(12):
        time.sleep(1)
        sys.stdout.write(f"\r      Waiting... {i+1}s / 12s")
        sys.stdout.flush()
    print("\n")

    # Query attempts for event B
    status, attempts_b = request(f"http://localhost:8000/api/events/{event_id_b}/attempts")
    print("   [Filter & DLQ check for payment.failed]:")
    attempted_route_ids_b = [att["route_id"] for att in attempts_b]
    route_c_attempts = [att for att in attempts_b if att["route_id"] == route_failed["id"]]
    
    print(f"   - Route A (No Filter): {'Attempted (matching always)' if route_all['id'] in attempted_route_ids_b else 'Skipped'}")
    print(f"   - Route B (Success Filter): {'Attempted' if route_success['id'] in attempted_route_ids_b else 'SKIPPED (Filter mismatch!)'}")
    print(f"   - Route C (Failed Filter): Attempted {len(route_c_attempts)} times (Failed connection, exhausted retries)")
    print()

    # 5. Verify DLQ status
    print("5. Querying Dead Letter Queue (DLQ)...")
    status, dlq_list = request("http://localhost:8000/api/dlq/")
    dlq_event = next((item for item in dlq_list if item["event_id"] == event_id_b), None)
    if dlq_event:
        print("   -> FOUND event in DLQ!")
        print(f"      Event ID: {dlq_event['event_id']}")
        print(f"      Last Destination: {dlq_event['last_url']}")
        print(f"      Last Error: {dlq_event['last_error']}")
    else:
        print("   -> Event NOT found in DLQ. Ensure transform & delivery workers are running!")
    print()

    print("=============================================================")
    print("                  DEMONSTRATION COMPLETE                     ")
    print("=============================================================")
    print("You can open your browser dashboard to view:")
    print("1. Overview tab: metrics updated.")
    print("2. Endpoints tab -> Configure: the 'E2E Video Demo Endpoint' and its routes.")
    print(f"3. Events Log tab: see events {event_id_a} and {event_id_b}.")
    print("4. Dead Letters tab: see the failed payment.failed event.")
    print("=============================================================")

if __name__ == "__main__":
    run_demo()
