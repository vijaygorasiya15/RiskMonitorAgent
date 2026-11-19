#!/usr/bin/env bash
# Smoke test against a running agent (local compose or any URL).
# Usage: scripts/smoke_test.sh [base_url]   (default http://localhost:8080)
set -euo pipefail
BASE="${1:-http://localhost:8080}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "ping..."
for i in $(seq 1 30); do
  curl -sf "$BASE/ping" >/dev/null && break
  [ "$i" = 30 ] && { echo "agent did not become healthy"; exit 1; }
  sleep 2
done

echo "invoke..."
OUT=$(curl -sf "$BASE/invocations" -H 'content-type: application/json' \
  -H "x-api-key: ${SMOKE_API_KEY:-demo-key-acme}" -d @"$HERE/examples/request_acme.json")
PY=$(command -v python || command -v python3)
echo "$OUT" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("status") == "ok", d
assert d["flagged_count"] >= 1, "expected at least one flagged transaction"
assert all(n["delivered"] for n in d["notifications"]), "webhook delivery failed"
print("OK: flagged", d["flagged_count"], "prompt", d["prompt_version"], "engine", d["engine"])
'
