"""End-to-end through the real HTTP app (mock engine, webhook transport faked)."""

import httpx
import pytest
from starlette.testclient import TestClient

import app.main as main
from app.config import ROOT
from app.tenants import load_tenants


@pytest.fixture
def client(monkeypatch, settings):
    sent = []

    class FakeClient(httpx.Client):
        def __init__(self, *a, **k):
            super().__init__(
                transport=httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200))[1])
            )

    monkeypatch.setattr("app.webhook.httpx.Client", FakeClient)
    main._state.cache_clear()
    monkeypatch.setattr(main, "_state", lambda: (settings, load_tenants(ROOT / "tenants.example.yaml")))
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_ping(client):
    assert client.get("/ping").status_code == 200


def test_invocation_flow(client):
    body = {
        "tenant_id": "acme",
        "transactions": [
            {
                "transaction_id": "t1",
                "amount": 9500,
                "merchant": "Crypto Hub",
                "timestamp": "2026-11-01T02:30:00Z",
                "customer_id": "c1",
            },
            {
                "transaction_id": "t2",
                "amount": 12,
                "merchant": "Cafe",
                "timestamp": "2026-11-01T13:00:00Z",
                "customer_id": "c2",
            },
        ],
    }
    r = client.post("/invocations", json=body, headers={"x-api-key": "demo-key-acme"})
    assert r.status_code == 200
    data = r.json()
    assert data["flagged_count"] == 1 and data["findings"][0]["transaction_id"] == "t1"
    assert data["notifications"][0]["delivered"] is True
    assert str(client.sent[0].url).endswith("/acme")


def test_invalid_payload_is_reported_not_crash(client):
    r = client.post("/invocations", json={"tenant_id": "acme", "transactions": [{"amount": -1}]})
    assert r.json()["error"] == "invalid_request"


def test_missing_api_key_rejected(client):
    body = {
        "tenant_id": "acme",
        "transactions": [
            {
                "transaction_id": "t1",
                "amount": 5,
                "merchant": "x",
                "timestamp": "2026-11-01T02:30:00Z",
                "customer_id": "c",
            }
        ],
    }
    assert client.post("/invocations", json=body).json()["error"] == "unauthorized"
