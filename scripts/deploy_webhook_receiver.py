"""Deploy / inspect / delete the demo webhook receiver (AWS Lambda + public function URL).

  deploy_webhook_receiver.py deploy   # create or update; prints the webhook URL to give a tenant
  deploy_webhook_receiver.py url      # print the webhook URL again
  deploy_webhook_receiver.py delete   # remove the function and its URL

Env: AWS_REGION, RECEIVER_ROLE_ARN (execution role, needed for deploy).
THIS CREATES AWS RESOURCES (Lambda free tier covers a demo): run only on purpose.
"""

from __future__ import annotations

import argparse
import io
import os
import secrets
import sys
import time
import zipfile
from pathlib import Path

import boto3

NAME = "risk-monitor-webhook-receiver"
SOURCE = Path(__file__).resolve().parent.parent / "webhook_receiver" / "lambda_function.py"
REGION = os.environ.get("AWS_REGION", "us-east-1")


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SOURCE, "lambda_function.py")
    return buf.getvalue()


def _wait_active(c) -> None:
    for _ in range(60):
        cfg = c.get_function_configuration(FunctionName=NAME)
        if cfg.get("State") == "Active" and cfg.get("LastUpdateStatus") in (None, "Successful"):
            return
        time.sleep(2)
    sys.exit("function did not become active")


def _url(c) -> str:
    cfg = c.get_function_configuration(FunctionName=NAME)
    base = c.get_function_url_config(FunctionName=NAME)["FunctionUrl"]
    return f"{base}{cfg['Environment']['Variables']['RECEIVER_SECRET']}/alerts"


def cmd_deploy(c) -> None:
    role = os.environ.get("RECEIVER_ROLE_ARN") or sys.exit("RECEIVER_ROLE_ARN is not set")
    try:
        cfg = c.get_function_configuration(FunctionName=NAME)
        env = cfg["Environment"]["Variables"]  # keep the existing secret so the URL stays stable
        c.update_function_code(FunctionName=NAME, ZipFile=_zip())
        _wait_active(c)
        print("updated code")
    except c.exceptions.ResourceNotFoundException:
        env = {"RECEIVER_SECRET": secrets.token_urlsafe(16)}
        c.create_function(
            FunctionName=NAME,
            Runtime="python3.12",
            Architectures=["arm64"],
            Role=role,
            Handler="lambda_function.handler",
            Code={"ZipFile": _zip()},
            Timeout=15,
            MemorySize=128,
            Environment={"Variables": env},
        )
        _wait_active(c)
        c.create_function_url_config(FunctionName=NAME, AuthType="NONE")
        # A public (AuthType NONE) function URL needs both permissions.
        c.add_permission(
            FunctionName=NAME,
            StatementId="PublicUrlInvoke",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
        c.add_permission(
            FunctionName=NAME,
            StatementId="PublicInvoke",
            Action="lambda:InvokeFunction",
            Principal="*",
            InvokedViaFunctionUrl=True,
        )
        print("created function and public URL")
    print("webhook URL:", _url(c))


def cmd_delete(c) -> None:
    for fn in (c.delete_function_url_config, c.delete_function):
        try:
            fn(FunctionName=NAME)
        except c.exceptions.ResourceNotFoundException:
            pass
    print("deleted")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["deploy", "url", "delete"])
    a = p.parse_args()
    c = boto3.client("lambda", region_name=REGION)
    {"deploy": cmd_deploy, "url": lambda c: print(_url(c)), "delete": cmd_delete}[a.cmd](c)


if __name__ == "__main__":
    main()
