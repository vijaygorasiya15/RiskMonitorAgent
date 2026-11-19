#!/usr/bin/env bash
# Free test of the AWS-facing release flow against a local AWS emulator (Floci).
# No real AWS account, no cost. The scripts under test are the SAME ones used for real AWS;
# the only difference is that AWS_ENDPOINT_URL points at the emulator here.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${FLOCI_HOST_PORT:-4567}"

docker compose -f docker-compose.floci.yml up -d
trap '[ "${KEEP_EMULATOR:-}" = 1 ] || docker compose -f docker-compose.floci.yml down -v' EXIT

for i in $(seq 1 30); do
  curl -sf "http://localhost:$PORT/" >/dev/null && break
  [ "$i" = 30 ] && { echo "emulator did not start"; exit 1; }
  sleep 2
done

export AWS_ENDPOINT_URL="http://localhost:$PORT"
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test # dummy: only valid for the emulator
if [ -x .venv/Scripts/python ]; then PY=.venv/Scripts/python
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=$(command -v python || command -v python3); fi
"$PY" scripts/e2e_deploy_flow.py
