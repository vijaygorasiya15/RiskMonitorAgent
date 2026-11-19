import pytest

from app.models import AnalyzeRequest, Finding, RiskAssessment, Severity
from app.service import RequestError, analyze
from tests.conftest import make_txn


def _req(tenant="acme", txns=None, **kw):
    txns = txns or [make_txn("ok1", 20), make_txn("big", 9000, hour=3)]
    return AnalyzeRequest(tenant_id=tenant, transactions=[t.model_dump(mode="json") for t in txns], **kw)


async def _run(settings, tenants, recorder, request, key="key-acme", **kw):
    return await analyze(request, settings=settings, tenants=tenants, api_key=key, http_client=recorder, **kw)


async def test_flags_and_notifies(settings, tenants, recorder):
    resp = await _run(settings, tenants, recorder, _req())
    assert resp.flagged_count == 1 and resp.findings[0].transaction_id == "big"
    assert resp.notifications[0].delivered
    assert len(recorder.calls) == 1


async def test_tenants_are_isolated(settings, tenants, recorder):
    """Each tenant's alerts go only to its own webhook; payloads carry its own id."""
    await _run(settings, tenants, recorder, _req("acme"), key="key-acme")
    await _run(settings, tenants, recorder, _req("globex"), key="key-globex")
    urls = [str(c.url) for c in recorder.calls]
    assert urls == ["http://hooks.test/acme", "http://hooks.test/globex"]


async def test_tenant_threshold_respected(settings, tenants, recorder):
    # medium-only finding (risky merchant): acme notified, globex (high only) not
    txns = [make_txn("m1", 100, merchant="Crypto Hub")]
    a = await _run(settings, tenants, recorder, _req("acme", txns), key="key-acme")
    g = await _run(settings, tenants, recorder, _req("globex", txns), key="key-globex")
    assert len(a.notifications) == 1
    assert g.flagged_count == 1 and g.notifications == []


async def test_wrong_key_and_unknown_tenant_same_error(settings, tenants, recorder):
    for tenant, key in [("acme", "wrong"), ("acme", None), ("nobody", "x")]:
        with pytest.raises(RequestError) as e:
            await _run(settings, tenants, recorder, _req(tenant), key=key)
        assert e.value.code == "unauthorized"
    assert recorder.calls == []


async def test_unknown_prompt_version(settings, tenants, recorder):
    with pytest.raises(RequestError) as e:
        await _run(settings, tenants, recorder, _req(prompt_version="v99"))
    assert e.value.code == "unknown_prompt_version"


async def test_too_many_and_duplicate_ids(settings, tenants, recorder):
    with pytest.raises(RequestError) as e:
        await _run(settings, tenants, recorder, _req(txns=[make_txn("d"), make_txn("d")]))
    assert e.value.code == "duplicate_transaction_id"


class FakeAgent:
    engine = "fake"

    def __init__(self, assessment, notify_ids=()):
        self.assessment, self.notify_ids = assessment, notify_ids

    def factory(self, settings, notifier, prompt):
        self.notifier = notifier
        return self

    async def assess(self, transactions):
        for tid in self.notify_ids:
            self.notifier.notify(tid, "high", "model notified")
        return self.assessment


async def test_hallucinated_ids_are_dropped(settings, tenants, recorder):
    fake = FakeAgent(
        RiskAssessment(findings=[Finding(transaction_id="ghost", severity=Severity.high, reason="x")])
    )
    resp = await _run(settings, tenants, recorder, _req(), agent_factory=fake.factory)
    assert resp.flagged_count == 0 and recorder.calls == []


async def test_missed_notification_is_reconciled(settings, tenants, recorder):
    """Model listed a finding but forgot to call the tool: service still notifies."""
    fake = FakeAgent(
        RiskAssessment(findings=[Finding(transaction_id="big", severity=Severity.high, reason="r")])
    )
    resp = await _run(settings, tenants, recorder, _req(), agent_factory=fake.factory)
    assert len(resp.notifications) == 1 and len(recorder.calls) == 1


async def test_tool_called_but_not_listed_is_included(settings, tenants, recorder):
    fake = FakeAgent(RiskAssessment(findings=[]), notify_ids=["big"])
    resp = await _run(settings, tenants, recorder, _req(), agent_factory=fake.factory)
    assert resp.flagged_count == 1


async def test_agent_failure_is_clean_error(settings, tenants, recorder):
    class Boom:
        engine = "boom"

        async def assess(self, t):
            raise RuntimeError("secret internal detail")

    with pytest.raises(RequestError) as e:
        await _run(settings, tenants, recorder, _req(), agent_factory=lambda *a: Boom())
    assert e.value.code == "agent_error" and "secret" not in e.value.message
