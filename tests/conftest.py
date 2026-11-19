import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROOT, load_settings  # noqa: E402
from app.models import Transaction  # noqa: E402
from app.tenants import Tenant  # noqa: E402


@pytest.fixture
def settings():
    s = load_settings(ROOT / "config.yaml")
    return replace(
        s,
        llm_mode="mock",
        webhook_allow_private=True,
        webhook_backoff_seconds=0.0,
        tenants_file=ROOT / "tenants.example.yaml",
    )


@pytest.fixture
def tenants():
    return {
        "acme": Tenant("acme", "http://hooks.test/acme", "medium", api_key="key-acme"),
        "globex": Tenant("globex", "http://hooks.test/globex", "high", api_key="key-globex"),
    }


def make_txn(tid="t1", amount=50.0, merchant="Corner Cafe", hour=14, customer="c1", minute=0):
    return Transaction(
        transaction_id=tid,
        amount=amount,
        merchant=merchant,
        timestamp=datetime(2026, 11, 1, hour, minute, tzinfo=UTC),
        customer_id=customer,
    )


@pytest.fixture
def recorder():
    """httpx client whose transport records requests instead of hitting the network."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls  # type: ignore[attr-defined]
    return client
