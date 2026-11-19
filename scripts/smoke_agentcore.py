"""Smoke-test a DEPLOYED AgentCore endpoint (e.g. "staging") before it is promoted to prod.

  AGENTCORE_RUNTIME_ID=... python scripts/smoke_agentcore.py --endpoint staging \
      --expect-prompt-version v2

Sends examples/request_smoke.json (one obviously fraudulent transaction among normal ones) to the
named endpoint and verifies the response. Exit code != 0 blocks the pipeline before promotion.

Requires a tenant named "smoke" in the runtime's TENANTS_JSON (its webhook can be any receiver you
control). Invoking uses IAM (SigV4) auth, so the CI role needs bedrock-agentcore:InvokeAgentRuntime.
With a real LLM this makes one small Bedrock call (a fraction of a cent).

--lenient only checks that the endpoint answers; used against a local emulator whose invoke is a stub.
Works against real AWS, or an emulator when AWS_ENDPOINT_URL is set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
REQUEST_FILE = ROOT / "examples" / "request_smoke.json"
FRAUD_ID = "s-2"  # the obviously suspicious transaction in the smoke request


def validate(body: dict, expected_prompt_version: str | None = None) -> list[str]:
    """Return a list of problems (empty = healthy). Pure function: unit-tested."""
    problems: list[str] = []
    if body.get("status") != "ok":
        return [f"status is {body.get('status')!r}, error={body.get('error')!r}"]
    if expected_prompt_version and body.get("prompt_version") != expected_prompt_version:
        problems.append(
            f"prompt_version is {body.get('prompt_version')!r}, expected {expected_prompt_version!r}"
        )
    if not str(body.get("engine", "")).startswith("bedrock:"):
        problems.append(f"engine is {body.get('engine')!r}; expected the real LLM (bedrock:*)")
    flagged = {f.get("transaction_id") for f in body.get("findings", [])}
    if FRAUD_ID not in flagged:
        problems.append(f"obvious fraud {FRAUD_ID} was not flagged (flagged: {sorted(flagged)})")
    if len(flagged) > 2:
        problems.append(f"{len(flagged)} of 3 transactions flagged: looks like a false-positive spike")
    undelivered = [n["transaction_id"] for n in body.get("notifications", []) if not n.get("delivered")]
    if undelivered:
        problems.append(f"webhook delivery failed for {undelivered}")
    if not body.get("notifications"):
        problems.append("no webhook notification was sent")
    return problems


def invoke(endpoint: str) -> dict:
    region = os.environ.get("AWS_REGION", "us-east-1")
    rid = os.environ["AGENTCORE_RUNTIME_ID"]
    arn = boto3.client("bedrock-agentcore-control", region_name=region).get_agent_runtime(agentRuntimeId=rid)[
        "agentRuntimeArn"
    ]
    resp = boto3.client("bedrock-agentcore", region_name=region).invoke_agent_runtime(
        agentRuntimeArn=arn,
        qualifier=endpoint,
        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex[:8],  # must be >= 33 chars
        contentType="application/json",
        accept="application/json",
        payload=REQUEST_FILE.read_bytes(),
    )
    raw = resp["response"].read()
    return json.loads(raw)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="staging")
    p.add_argument("--expect-prompt-version")
    p.add_argument("--lenient", action="store_true")
    a = p.parse_args()
    if "AGENTCORE_RUNTIME_ID" not in os.environ:
        sys.exit("AGENTCORE_RUNTIME_ID is not set")

    body = invoke(a.endpoint)
    if a.lenient:
        print(f"OK (lenient): endpoint '{a.endpoint}' answered: {json.dumps(body)[:120]}")
        return
    problems = validate(body, a.expect_prompt_version)
    if problems:
        print(f"SMOKE TEST FAILED on '{a.endpoint}':")
        for pr in problems:
            print(f"  - {pr}")
        sys.exit(1)
    flagged, version = body["flagged_count"], body["prompt_version"]
    print(f"SMOKE TEST PASSED on '{a.endpoint}': flagged {flagged}, prompt {version}")


if __name__ == "__main__":
    main()
