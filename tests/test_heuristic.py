from app.heuristic import evaluate
from tests.conftest import make_txn


def test_normal_batch_has_no_findings(settings):
    out = evaluate([make_txn("a", 20), make_txn("b", 35, hour=12, customer="c2")], settings)
    assert out.findings == []


def test_large_amount_and_night_is_high(settings):
    out = evaluate([make_txn("a", 9000, hour=3)], settings)
    assert len(out.findings) == 1 and out.findings[0].severity.value == "high"


def test_risky_merchant(settings):
    out = evaluate([make_txn("a", 100, merchant="QuickCrypto Exchange")], settings)
    assert out.findings[0].transaction_id == "a"


def test_velocity(settings):
    txns = [make_txn(f"v{i}", 10, customer="c9", minute=i) for i in range(3)]
    out = evaluate(txns, settings)
    assert {f.transaction_id for f in out.findings} == {"v0", "v1", "v2"}
