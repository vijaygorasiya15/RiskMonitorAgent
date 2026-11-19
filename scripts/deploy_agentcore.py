"""Deploy / promote / roll back the agent on AWS Bedrock AgentCore Runtime.

Every `update-runtime` creates a NEW immutable runtime version. Customers talk to a
named endpoint (e.g. "prod"), which points at one version. Promoting or rolling back
only repoints the endpoint, so rollback takes seconds and needs no rebuild.

  deploy_agentcore.py deploy   --image <ecr-uri:sha> [--endpoint staging]
  deploy_agentcore.py promote  --version 5 [--endpoint prod]
  deploy_agentcore.py rollback [--endpoint prod]          # previous version
  deploy_agentcore.py rollback --version 3 [--endpoint prod]
  deploy_agentcore.py status

Env: AWS_REGION, AGENTCORE_RUNTIME_ID, AGENTCORE_ROLE_ARN (for deploy).
Target: real AWS by default. If AWS_ENDPOINT_URL is set (e.g. a local emulator), boto3 uses it
automatically, so the exact same script works against both with no code change.
THIS CREATES / CHANGES AWS RESOURCES (costs money): run only on purpose.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import boto3


def client():
    return boto3.client("bedrock-agentcore-control", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def runtime_id() -> str:
    try:
        return os.environ["AGENTCORE_RUNTIME_ID"]
    except KeyError:
        sys.exit("AGENTCORE_RUNTIME_ID is not set")


def wait_ready(c, rid: str, endpoint: str | None = None, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if endpoint:
            r = c.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=endpoint)
        else:
            r = c.get_agent_runtime(agentRuntimeId=rid)
        status = r["status"]
        if status == "READY":
            return r
        if status.endswith("FAILED"):
            sys.exit(f"{status}: {r.get('failureReason')}")
        time.sleep(5)
    sys.exit("timed out waiting for READY")


def versions(c, rid: str) -> list[str]:
    out, token = [], None
    while True:
        kw = {"agentRuntimeId": rid, **({"nextToken": token} if token else {})}
        r = c.list_agent_runtime_versions(**kw)
        out += [v["agentRuntimeVersion"] for v in r["agentRuntimes"]]
        token = r.get("nextToken")
        if not token:
            break
    return sorted(out, key=int)


def point_endpoint(c, rid: str, endpoint: str, version: str) -> None:
    try:
        c.update_agent_runtime_endpoint(
            agentRuntimeId=rid, endpointName=endpoint, agentRuntimeVersion=version
        )
    except c.exceptions.ResourceNotFoundException:
        c.create_agent_runtime_endpoint(agentRuntimeId=rid, name=endpoint, agentRuntimeVersion=version)
    wait_ready(c, rid, endpoint)
    print(f"endpoint '{endpoint}' -> version {version}")


def cmd_deploy(a) -> None:
    c, rid = client(), runtime_id()
    cur = c.get_agent_runtime(agentRuntimeId=rid)
    env = dict(cur.get("environmentVariables") or {})
    env["PROMPT_VERSION"] = a.prompt_version or env.get("PROMPT_VERSION", "v1")
    env["LLM_MODE"] = "bedrock"
    # UpdateAgentRuntime replaces the whole config: carry over everything we are not changing,
    # otherwise omitted settings (protocol, authorizer, ...) would be cleared.
    keep = {
        k: cur[k]
        for k in (
            "protocolConfiguration",
            "authorizerConfiguration",
            "requestHeaderConfiguration",
            "lifecycleConfiguration",
            "description",
        )
        if cur.get(k)
    }
    c.update_agent_runtime(
        agentRuntimeId=rid,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": a.image}},
        roleArn=os.environ.get("AGENTCORE_ROLE_ARN") or cur["roleArn"],
        networkConfiguration=cur["networkConfiguration"],
        environmentVariables=env,
        **keep,
    )
    new = wait_ready(c, rid)
    version = new["agentRuntimeVersion"]
    print(f"new runtime version {version} ({a.image})")
    if os.environ.get("GITHUB_OUTPUT"):  # hand the version to the promote job
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"version={version}\n")
    point_endpoint(c, rid, a.endpoint, version)  # staging first; promote to prod after smoke test


def cmd_promote(a) -> None:
    point_endpoint(client(), runtime_id(), a.endpoint, a.version)


def cmd_rollback(a) -> None:
    c, rid = client(), runtime_id()
    target = a.version
    if not target:
        cur = c.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName=a.endpoint)
        vs = versions(c, rid)
        live = cur.get("liveVersion") or cur.get("targetVersion")
        earlier = [v for v in vs if int(v) < int(live)]
        if not earlier:
            sys.exit("no earlier version to roll back to")
        target = earlier[-1]
    point_endpoint(c, rid, a.endpoint, target)


def cmd_status(_a) -> None:
    c, rid = client(), runtime_id()
    print("versions:", versions(c, rid))
    for ep in c.list_agent_runtime_endpoints(agentRuntimeId=rid)["runtimeEndpoints"]:
        print(ep["name"], ep.get("liveVersion"), ep["status"])


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--image", required=True)
    d.add_argument("--endpoint", default="staging")
    d.add_argument("--prompt-version")
    d.set_defaults(fn=cmd_deploy)
    pr = sub.add_parser("promote")
    pr.add_argument("--version", required=True)
    pr.add_argument("--endpoint", default="prod")
    pr.set_defaults(fn=cmd_promote)
    r = sub.add_parser("rollback")
    r.add_argument("--version")
    r.add_argument("--endpoint", default="prod")
    r.set_defaults(fn=cmd_rollback)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
