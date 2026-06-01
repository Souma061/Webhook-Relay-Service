import hmac
import hashlib
import urllib.request
import urllib.error
import json
import time

# ---------------------------------------------------------
# Webhook Relay Test Script
# ---------------------------------------------------------

# Using the "Webhook site" endpoint we found in your database
ENDPOINT_ID = "db8e870c-998c-4eee-a29b-2338c73fae86"
SECRET = b"1a7dbc16d484dbcd8744941d4b7d9edf8d164a8b3f80c554a752e4d43a447d0b"

# The data we want to send
payload = {
    "event_type": "payment.succeeded",
    "amount": 9900,
    "currency": "usd",
    "customer": "cus_12345",
    "timestamp": int(time.time())
}

# 1. Prepare the raw body (must match exactly what we hash)
raw_body = json.dumps(payload).encode('utf-8')

# 2. Compute the HMAC SHA-256 signature
signature = hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()

print(f"Sending payload to Endpoint: {ENDPOINT_ID}")
print(f"Computed Signature: sha256={signature}\n")

# 3. Send the request to the local gateway using built-in urllib
req = urllib.request.Request(
    f"http://localhost:8000/hooks/{ENDPOINT_ID}",
    data=raw_body,
    headers={
        "Content-Type": "application/json",
        "x-hub-signature-256": f"sha256={signature}",
        "idempotency-key": f"test-idem-key-{int(time.time())}"
    },
    method="POST"
)

try:
    with urllib.request.urlopen(req) as response:
        status_code = response.getcode()
        response_text = response.read().decode('utf-8')

        print(f"Gateway Status: {status_code}")
        print(f"Gateway Response: {response_text}")

        if status_code == 202:
            print("\nSuccess! The webhook was accepted by the gateway.")
            print("You should see it appear on your webhook.site page in a few milliseconds!")
        else:
            print("\nHmm, something went wrong. Check the app logs.")

except urllib.error.HTTPError as e:
    print(f"Gateway Error Status: {e.code}")
    print(f"Gateway Error Response: {e.read().decode('utf-8')}")
except urllib.error.URLError:
    print("\nError: Could not connect to the gateway. Is the Docker container running?")
