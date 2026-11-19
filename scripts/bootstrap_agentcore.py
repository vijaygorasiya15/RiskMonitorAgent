"""Create the AgentCore runtime once (idempotent) and print its runtime ID.

The pipeline's deploy step then only *updates* this runtime (new immutable version each time).

  AGENTCORE_ROLE_ARN=arn:aws:iam::<acct>:role/<execution-role> \
  python scripts/bootstrap_agentcore.py --image <ecr-uri:tag> [--name risk_monitor]

Works against real AWS, or a local emulator when AWS_ENDPOINT_URL is set.
On real AWS this creates a resource: ask before running it.
"""

from __future__ import annotations

import argparse
import os
import sys

import boto3


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--name", default="risk_monitor")  # AgentCore names: letters, digits, underscore
    p.add_argument("--prompt-version", default="v1")
    a = p.parse_args()

    role = os.environ.get("AGENTCORE_ROLE_ARN")
    if not role:
        sys.exit("AGENTCORE_ROLE_ARN is not set")
    c = boto3.client("bedrock-agentcore-control", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    existing = [
        r for r in c.list_agent_runtimes().get("agentRuntimes", []) if r["agentRuntimeName"] == a.name
    ]
    if existing:
        rid = existing[0]["agentRuntimeId"]
        print(f"runtime '{a.name}' already exists", file=sys.stderr)
    else:
        env = {"LLM_MODE": "bedrock", "PROMPT_VERSION": a.prompt_version}
        if os.environ.get("TENANTS_JSON"):  # tenant webhooks/keys, incl. the "smoke" tenant
            env["TENANTS_JSON"] = os.environ["TENANTS_JSON"]
        rid = c.create_agent_runtime(
            agentRuntimeName=a.name,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": a.image}},
            roleArn=role,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables=env,
        )["agentRuntimeId"]
        print(f"created runtime '{a.name}'", file=sys.stderr)
    print(rid)


if __name__ == "__main__":
    main()
