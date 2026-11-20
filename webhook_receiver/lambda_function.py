"""A small, real webhook receiver for demos, run as an AWS Lambda behind a public function URL.

POST /<secret>/alerts  -> stores the alert (writes it to the function's CloudWatch log)
GET  /<secret>/alerts  -> an HTML page listing the most recent alerts (read back from the same log)

Access control: the URL contains a random secret path segment (RECEIVER_SECRET); any other
path gets a 404. Alerts are held only in CloudWatch Logs, so there is no database to manage.
Demo-grade: for production, use the customer's own endpoint and real authentication.
"""

from __future__ import annotations

import base64
import html
import json
import os
import time

MARK = "ALERT_RECEIVED "
MAX_BODY = 64 * 1024  # bytes: ignore anything bigger
SHOW = 20  # alerts shown on the page


def _resp(code: int, body: str, ctype: str = "application/json") -> dict:
    return {"statusCode": code, "headers": {"content-type": ctype}, "body": body}


def _secret() -> str:
    return os.environ.get("RECEIVER_SECRET", "")


def _logs_client():
    import boto3  # imported lazily so tests do not need AWS configuration

    return boto3.client("logs")


def _recent_alerts(client, log_group: str) -> list[dict]:
    start = int((time.time() - 24 * 3600) * 1000)
    out: list[dict] = []
    token = None
    while True:
        kw = {"logGroupName": log_group, "filterPattern": f'"{MARK.strip()}"', "startTime": start}
        if token:
            kw["nextToken"] = token
        r = client.filter_log_events(**kw)
        for ev in r.get("events", []):
            msg = ev["message"]
            if MARK in msg:
                try:
                    out.append(json.loads(msg.split(MARK, 1)[1]))
                except ValueError:
                    continue
        token = r.get("nextToken")
        if not token or len(out) > 500:
            break
    out.sort(key=lambda a: a.get("received_at", 0), reverse=True)
    return out[:SHOW]


def render_page(alerts: list[dict]) -> str:
    rows = []
    for a in alerts:
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(a.get("received_at", 0)))
        body = html.escape(json.dumps(a.get("payload"), indent=2))
        key = html.escape(str(a.get("idempotency_key")))
        rows.append(f"<tr><td>{when}</td><td><code>{key}</code></td><td><pre>{body}</pre></td></tr>")
    table = "".join(rows) or "<tr><td colspan=3>No alerts received yet.</td></tr>"
    return (
        "<!doctype html><meta charset=utf-8><title>Received alerts</title>"
        "<style>body{font:14px system-ui;margin:2rem}td,th{border:1px solid #ccc;padding:6px;"
        "vertical-align:top}table{border-collapse:collapse}pre{margin:0}</style>"
        f"<h2>Received alerts (latest {SHOW}, last 24h)</h2>"
        "<p>Log delivery can lag a few seconds: refresh if an alert is missing.</p>"
        f"<table><tr><th>Received</th><th>Idempotency-Key</th><th>Payload</th></tr>{table}</table>"
    )


def handler(event, context):
    secret = _secret()
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    if not secret or not path.startswith(f"/{secret}/"):
        return _resp(404, '{"error":"not found"}')

    if method == "POST":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        if len(raw.encode("utf-8")) > MAX_BODY:
            return _resp(413, '{"error":"payload too large"}')
        try:
            payload = json.loads(raw)
        except ValueError:
            return _resp(400, '{"error":"invalid json"}')
        headers = event.get("headers") or {}
        print(
            MARK
            + json.dumps(
                {
                    "received_at": time.time(),
                    "idempotency_key": headers.get("idempotency-key"),
                    "payload": payload,
                }
            )
        )
        return _resp(200, '{"received":true}')

    if method == "GET":
        group = os.environ.get("AWS_LAMBDA_LOG_GROUP_NAME", "")
        alerts = _recent_alerts(_logs_client(), group) if group else []
        return _resp(200, render_page(alerts), "text/html; charset=utf-8")

    return _resp(405, '{"error":"method not allowed"}')
