import importlib.util
import json
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "webhook_receiver" / "lambda_function.py"


@pytest.fixture
def rx(monkeypatch):
    monkeypatch.setenv("RECEIVER_SECRET", "s3cret")
    spec = importlib.util.spec_from_file_location("rx_lambda", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(method, path, body=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path, "body": body}


def test_wrong_secret_is_404(rx):
    assert rx.handler(event("POST", "/nope/alerts", "{}"), None)["statusCode"] == 404
    assert rx.handler(event("GET", "/alerts"), None)["statusCode"] == 404


def test_post_logs_alert(rx, capsys):
    ev = event("POST", "/s3cret/alerts", json.dumps({"transaction_id": "s-2"}))
    ev["headers"] = {"idempotency-key": "abc"}
    assert rx.handler(ev, None)["statusCode"] == 200
    line = capsys.readouterr().out.strip()
    assert line.startswith(rx.MARK)
    stored = json.loads(line[len(rx.MARK) :])
    assert stored["payload"] == {"transaction_id": "s-2"}
    assert stored["idempotency_key"] == "abc"


def test_bad_json_rejected(rx):
    assert rx.handler(event("POST", "/s3cret/alerts", "not json"), None)["statusCode"] == 400


def test_page_escapes_html(rx):
    page = rx.render_page([{"received_at": 0, "idempotency_key": "k", "payload": {"x": "<script>"}}])
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
