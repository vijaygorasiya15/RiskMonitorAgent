"""Per-tenant configuration. Each tenant has its own webhook URL and API key.

Tenant data is never merged: every request resolves exactly one Tenant, and the
webhook client created for that request can only reach that tenant's URL.
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class TenantError(Exception):
    """Unknown tenant or failed authentication."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    webhook_url: str
    notify_min_severity: str = "medium"
    api_key: str | None = None  # None => no key required (local demo only)


def _build(entries: dict) -> dict[str, Tenant]:
    tenants: dict[str, Tenant] = {}
    for tid, entry in (entries or {}).items():
        # Webhook URL / API key may be given literally or via an env var name,
        # so real secrets never need to live in a file in git.
        url = entry.get("webhook_url") or os.getenv(entry.get("webhook_url_env", ""), "")
        api_key = entry.get("api_key") or os.getenv(entry.get("api_key_env", ""), "") or None
        if not url:
            raise ValueError(f"tenant '{tid}' has no webhook url configured")
        tenants[tid] = Tenant(
            tenant_id=tid,
            webhook_url=url,
            notify_min_severity=entry.get("notify_min_severity", "medium"),
            api_key=api_key,
        )
    return tenants


def load_tenants(path: Path) -> dict[str, Tenant]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _build(raw.get("tenants") or {})


def load_configured_tenants(path: Path) -> dict[str, Tenant]:
    """Tenants come from the TENANTS_JSON env var when set (how a cloud runtime is configured,
    since its container filesystem is read-only config baked into the image), else from a file.

    TENANTS_JSON: {"acme": {"webhook_url": "...", "api_key": "...", "notify_min_severity": "high"}}
    Production should inject this from a secret store (e.g. AWS Secrets Manager), not plain config.
    """
    raw = os.getenv("TENANTS_JSON", "").strip()
    if raw:
        return _build(json.loads(raw))
    return load_tenants(path)


def authenticate(tenants: dict[str, Tenant], tenant_id: str, presented_key: str | None) -> Tenant:
    tenant = tenants.get(tenant_id)
    if tenant is None:
        raise TenantError("unknown tenant")
    if tenant.api_key is not None:
        if not presented_key or not hmac.compare_digest(tenant.api_key, presented_key):
            raise TenantError("invalid api key")
    return tenant
