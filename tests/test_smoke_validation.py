"""The staging smoke-test decision logic: what blocks a promotion to prod."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import smoke_agentcore as smoke  # noqa: E402

from app.models import AnalyzeRequest  # noqa: E402


def healthy(**over):
    body = {
        "status": "ok",
        "prompt_version": "v2",
        "engine": "bedrock:some-model",
        "findings": [{"transaction_id": "s-2", "severity": "high", "reason": "crypto at 2am"}],
        "notifications": [{"transaction_id": "s-2", "delivered": True, "attempts": 1}],
    }
    body.update(over)
    return body


def test_healthy_response_passes():
    assert smoke.validate(healthy(), "v2") == []


def test_wrong_prompt_version_blocks():
    assert any("prompt_version" in p for p in smoke.validate(healthy(prompt_version="v1"), "v2"))


def test_missed_fraud_blocks():
    assert any("not flagged" in p for p in smoke.validate(healthy(findings=[]), "v2"))


def test_false_positive_spike_blocks():
    findings = [{"transaction_id": t, "severity": "medium", "reason": "x"} for t in ("s-1", "s-2", "s-3")]
    assert any("false-positive" in p for p in smoke.validate(healthy(findings=findings), "v2"))


def test_failed_webhook_blocks():
    bad = [{"transaction_id": "s-2", "delivered": False, "attempts": 3}]
    assert any("delivery failed" in p for p in smoke.validate(healthy(notifications=bad), "v2"))


def test_mock_engine_blocks():
    """A deployed runtime must be using the real LLM, not the rule-based mock."""
    assert any("engine" in p for p in smoke.validate(healthy(engine="mock-rules"), "v2"))


def test_error_response_blocks():
    assert smoke.validate({"status": "error", "error": "unauthorized"})


def test_smoke_request_file_is_valid_for_the_agent():
    data = json.loads(smoke.REQUEST_FILE.read_text(encoding="utf-8"))
    req = AnalyzeRequest.model_validate(data)
    assert req.tenant_id == "smoke" and smoke.FRAUD_ID in {t.transaction_id for t in req.transactions}


@pytest.mark.parametrize("tenants_json_present", [True, False])
def test_tenants_from_env_json(monkeypatch, tenants_json_present):
    from app.config import ROOT
    from app.tenants import load_configured_tenants

    if tenants_json_present:
        monkeypatch.setenv("TENANTS_JSON", json.dumps({"smoke": {"webhook_url": "https://hooks.example/x"}}))
        assert set(load_configured_tenants(ROOT / "tenants.example.yaml")) == {"smoke"}
    else:
        monkeypatch.delenv("TENANTS_JSON", raising=False)
        assert {"acme", "globex"} <= set(load_configured_tenants(ROOT / "tenants.example.yaml"))
