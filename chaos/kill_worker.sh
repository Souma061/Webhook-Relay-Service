#!/usr/bin/env bash
# Kill a worker mid-stream and watch consumer rebalancing in Kafka UI.
set -euo pipefail

WORKER="${1:-delivery}"  # delivery | transform | retry

echo "Killing worker container: webhook-relay-service-${WORKER}-worker-1"
docker compose -f docker-compose.dev.yml stop "${WORKER}-worker"

echo ""
echo "=== Now open http://localhost:8081 in your browser ==="
echo "  -> Go to Consumers tab"
echo "  -> Watch rebalance happen as the consumer group detects the loss"
echo "  -> docker compose -f docker-compose.dev.yml start ${WORKER}-worker  to restart"
