import json

import httpx
import pytest

from app.webhook import Notifier, WebhookURLError, validate_webhook_url
from tests.conftest import make_txn


def _notifier(client, **kw):
    return Notifier(
        tenant_id="acme",
        webhook_url="http://hooks.test/acme",
        request_id="r1",
        prompt_version="v1",
        transactions=[make_txn("t1", 9000)],
        allow_private=True,
        backoff=0.0,
        client=client,
        **kw,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",  # not https
        "https://127.0.0.1/hook",  # loopback
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://10.0.0.5/hook",  # private
        "ftp://example.com/hook",
        "not-a-url",
    ],
)
def test_ssrf_blocked(url):
    with pytest.raises(WebhookURLError):
        validate_webhook_url(url, allow_private=False)


def test_notify_sends_validated_transaction_and_idempotency_key(recorder):
    n = _notifier(recorder)
    res = n.notify("t1", "high", "big amount")
    assert res.delivered and res.attempts == 1
    req = recorder.calls[0]
    body = json.loads(req.content)
    assert body["tenant_id"] == "acme"
    assert body["transaction"]["amount"] == 9000  # from validated input, not LLM
    assert req.headers["Idempotency-Key"]


def test_unknown_transaction_rejected(recorder):
    with pytest.raises(KeyError):
        _notifier(recorder).notify("hallucinated", "high", "x")
    assert recorder.calls == []


def test_dedupe_per_request(recorder):
    n = _notifier(recorder)
    n.notify("t1", "high", "a")
    n.notify("t1", "high", "b")
    assert len(recorder.calls) == 1


def test_retries_on_5xx_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503 if len(attempts) < 3 else 200)

    n = _notifier(httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=3)
    res = n.notify("t1", "high", "x")
    assert res.delivered and res.attempts == 3


def test_no_retry_on_4xx():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(400)

    n = _notifier(httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=3)
    res = n.notify("t1", "high", "x")
    assert not res.delivered and len(attempts) == 1


def test_gives_up_and_reports_failure():
    def handler(request):
        raise httpx.ConnectError("boom")

    n = _notifier(httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=2)
    res = n.notify("t1", "high", "x")
    assert not res.delivered and res.attempts == 2 and res.error == "ConnectError"
