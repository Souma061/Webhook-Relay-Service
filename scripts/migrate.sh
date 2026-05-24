#!/usr/bin/env bash
# scripts/migrate.sh — Apply incremental schema changes to a running Postgres instance.
# Run this script once after pulling new code that adds DB columns.
# Safe to re-run: uses ADD COLUMN IF NOT EXISTS.
#
# Usage:
#   bash scripts/migrate.sh
#   PGPASSWORD=<pw> bash scripts/migrate.sh   # if POSTGRES_PASSWORD is non-default
#
# Assumes docker compose is up and the postgres service is named 'postgres'.

set -euo pipefail

CONTAINER=$(docker compose ps -q postgres)
if [[ -z "$CONTAINER" ]]; then
  echo "Error: postgres container is not running. Start it first with: docker compose up -d postgres"
  exit 1
fi

echo "▶ Applying Phase 3 schema migrations..."

docker exec "$CONTAINER" psql -U postgres -d webhook_relay <<'SQL'
-- Phase 3: DLQ soft-delete support
ALTER TABLE events ADD COLUMN IF NOT EXISTS is_discarded  BOOLEAN      DEFAULT false NOT NULL;
ALTER TABLE events ADD COLUMN IF NOT EXISTS discarded_at  TIMESTAMPTZ  DEFAULT NULL;
SQL

echo "✔ Migration complete."
