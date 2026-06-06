# Chaos Experiments

Prerequisite: `demo_webhook_site.py` has been run at least once to create an endpoint.

```bash
cd /home/soumabrata/Workspace/Experiments/webhook-relay-service

# 1. Find your endpoint ID
psql -h localhost -p 5433 -U webhook_user -d webhook_relay -c "SELECT id, name FROM endpoints LIMIT 5;"

# 2. Run the scripts below against that endpoint ID
```
