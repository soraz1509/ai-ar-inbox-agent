#!/usr/bin/env bash
# Test-only reset: drafts in the S/4 mock + AR case tables. Not part of the product.
set -e
cd "$(dirname "$0")/.."
KEY=$(grep '^EXECUTOR_KEY=' .env | cut -d= -f2-)
curl -s -X POST http://localhost:8001/mock/reset-drafts -H "X-Executor-Key: $KEY"; echo
docker compose exec -T postgres psql -U ar -d ar_demo -c "TRUNCATE ar.case_event, ar.ar_case, ar.inbound_email RESTART IDENTITY;"
