"""End-to-end test of the AWS-facing release flow, against whatever AWS_ENDPOINT_URL points at.

Runs the same CLI commands the pipeline runs:
  bootstrap -> deploy(image 2)->staging -> promote prod -> deploy(image 3)->staging -> promote prod
  -> rollback prod -> assert prod is back on the previous version.

Free against a local emulator:  scripts/aws_emulator_e2e.sh
Against real AWS: real credentials, AWS_ENDPOINT_URL unset, and pass --real. It creates a
runtime with a throwaway name and deletes it at the end. Ask before running.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
REGISTRY = os.environ.get("E2E_REGISTRY", "000000000000.dkr.ecr.us-east-1.amazonaws.com")


def run(script: str, *args: str, env: dict | None = None) -> str:
    out = subprocess.run(
        [sys.executable, str(HERE / script), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return out.stdout.strip()


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def image(tag: str) -> str:
    return f"{REGISTRY}/risk-monitor:{tag}"


def main() -> None:
    if not os.environ.get("AWS_ENDPOINT_URL") and "--real" not in sys.argv:
        sys.exit("AWS_ENDPOINT_URL not set: refusing to touch real AWS without --real")
    region = os.environ.get("AWS_REGION", "us-east-1")
    c = boto3.client("bedrock-agentcore-control", region_name=region)
    name = "risk_monitor_e2e_" + uuid.uuid4().hex[:6]
    role = os.environ.setdefault("AGENTCORE_ROLE_ARN", "arn:aws:iam::000000000000:role/agent-runtime")

    rid = run("bootstrap_agentcore.py", "--image", image("sha-1"), "--name", name)
    env = {"AGENTCORE_RUNTIME_ID": rid, "AGENTCORE_ROLE_ARN": role}

    def live(endpoint: str) -> str | None:
        return c.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=endpoint)["liveVersion"]

    try:
        again = run("bootstrap_agentcore.py", "--image", image("sha-1"), "--name", name)
        check(again == rid, "bootstrap is idempotent (same runtime id)")

        run("deploy_agentcore.py", "deploy", "--image", image("sha-2"), "--prompt-version", "v1", env=env)
        # Staging gate. An emulator's invoke is a stub, so only check the endpoint answers; the
        # strict content checks are unit-tested (tests/test_smoke_validation.py) and run on real AWS.
        out = run("smoke_agentcore.py", "--endpoint", "staging", "--lenient", env=env)
        check(out.startswith("OK (lenient)"), "staging endpoint is invokable by qualifier (smoke gate)")
        run("deploy_agentcore.py", "promote", "--version", "2", env=env)
        run("deploy_agentcore.py", "deploy", "--image", image("sha-3"), "--prompt-version", "v2", env=env)
        run("deploy_agentcore.py", "promote", "--version", "3", env=env)

        check(live("prod") == "3", "prod serves version 3 after promote")
        check(live("staging") == "3", "staging serves version 3")

        rt = c.get_agent_runtime(agentRuntimeId=rid)
        check(
            rt["environmentVariables"].get("PROMPT_VERSION") == "v2", "prompt version flowed to runtime env"
        )
        uri = rt["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
        check(uri == image("sha-3"), "runtime points at the new image (git-SHA tag)")
        protocol = rt.get("protocolConfiguration", {}).get("serverProtocol")
        check(protocol == "HTTP", "protocol config preserved across updates")

        run("deploy_agentcore.py", "rollback", env=env)
        check(live("prod") == "2", "rollback moved prod back to version 2")
        check(live("staging") == "3", "rollback did not touch staging")

        run("deploy_agentcore.py", "rollback", "--version", "1", env=env)
        check(live("prod") == "1", "rollback to an explicit version works")
        print("\nALL PASSED")
    finally:
        c.delete_agent_runtime(agentRuntimeId=rid)
        print("cleaned up runtime", rid)


if __name__ == "__main__":
    main()
