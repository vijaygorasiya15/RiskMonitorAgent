"""Orchestration: authenticate -> pick prompt -> run agent -> reconcile -> respond."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable

import httpx

from .agent import RiskAgent, build_agent
from .config import Settings
from .models import AnalyzeRequest, AnalyzeResponse, Finding
from .tenants import Tenant, authenticate
from .webhook import Notifier

log = logging.getLogger("risk_monitor.service")

AgentFactory = Callable[[Settings, Notifier, str], RiskAgent]


class RequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_prompt(settings: Settings, version: str) -> str:
    path = (settings.prompts_dir / f"{version}.md").resolve()
    if settings.prompts_dir.resolve() not in path.parents or not path.is_file():
        raise RequestError("unknown_prompt_version", f"prompt version {version!r} does not exist")
    return path.read_text(encoding="utf-8")


async def analyze(
    request: AnalyzeRequest,
    *,
    settings: Settings,
    tenants: dict[str, Tenant],
    api_key: str | None = None,
    agent_factory: AgentFactory = build_agent,
    http_client: httpx.Client | None = None,
) -> AnalyzeResponse:
    from .tenants import TenantError

    try:
        tenant = authenticate(tenants, request.tenant_id, api_key)
    except TenantError as exc:
        # same message for unknown tenant / bad key: don't reveal which tenants exist
        raise RequestError("unauthorized", "unknown tenant or invalid credentials") from exc

    if len(request.transactions) > settings.max_transactions:
        raise RequestError(
            "too_many_transactions", f"max {settings.max_transactions} transactions per request"
        )

    ids = [t.transaction_id for t in request.transactions]
    if len(set(ids)) != len(ids):
        raise RequestError("duplicate_transaction_id", "transaction_id values must be unique")

    version = request.prompt_version or settings.active_prompt_version
    system_prompt = load_prompt(settings, version)
    request_id = uuid.uuid4().hex

    notifier = Notifier(
        tenant_id=tenant.tenant_id,
        webhook_url=tenant.webhook_url,
        request_id=request_id,
        prompt_version=version,
        transactions=request.transactions,
        min_severity=tenant.notify_min_severity,
        allow_private=settings.webhook_allow_private,
        timeout=settings.webhook_timeout_seconds,
        max_attempts=settings.webhook_max_attempts,
        backoff=settings.webhook_backoff_seconds,
        client=http_client,
    )
    agent = agent_factory(settings, notifier, system_prompt)

    try:
        assessment = await agent.assess(request.transactions)
    except Exception as exc:  # LLM / network failure: report cleanly, don't leak internals
        log.exception("agent failed tenant=%s request=%s", tenant.tenant_id, request_id)
        raise RequestError("agent_error", f"analysis failed: {type(exc).__name__}") from exc

    # Reconcile. Never trust the model blindly:
    #  - drop findings that reference transaction ids that were not in the input
    #  - make sure every finding at/above the tenant threshold was actually notified
    #  - include anything the model notified about but forgot to list
    known = set(ids)
    findings: dict[str, Finding] = {
        f.transaction_id: f for f in assessment.findings if f.transaction_id in known
    }
    for tid, f in notifier.sent.items():
        findings.setdefault(tid, f)

    for tid, f in findings.items():
        if tid not in notifier.results:  # notifier itself enforces the tenant threshold
            await asyncio.to_thread(notifier.notify, tid, f.severity.value, f.reason)

    ordered = sorted(findings.values(), key=lambda f: (-f.severity.rank, f.transaction_id))
    return AnalyzeResponse(
        request_id=request_id,
        tenant_id=tenant.tenant_id,
        prompt_version=version,
        engine=agent.engine,
        total_transactions=len(request.transactions),
        flagged_count=len(ordered),
        findings=ordered,
        notifications=list(notifier.results.values()),
        summary=assessment.summary or f"{len(ordered)} of {len(ids)} transactions flagged.",
    )
