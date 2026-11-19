"""Webhook notifier: tenant-bound, SSRF-guarded, retried, idempotent."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

import httpx

from .models import Finding, NotificationResult, Severity, Transaction

log = logging.getLogger("risk_monitor.webhook")


class WebhookURLError(ValueError):
    pass


def validate_webhook_url(url: str, allow_private: bool = False) -> None:
    """Reject URLs that could be used for SSRF (internal IPs, metadata service)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise WebhookURLError("webhook must be an http(s) URL")
    if allow_private:  # local demo only (docker-compose mock receiver)
        return
    if parsed.scheme != "https":
        raise WebhookURLError("webhook must use https")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookURLError(f"cannot resolve webhook host: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise WebhookURLError("webhook resolves to a non-public address")


class Notifier:
    """Sends risk notifications for ONE request of ONE tenant.

    The URL is fixed at construction, so neither the LLM nor request payload can
    redirect a notification to another destination. Transaction details are taken
    from the validated input, never from LLM output.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        webhook_url: str,
        request_id: str,
        prompt_version: str,
        transactions: list[Transaction],
        min_severity: str = "medium",
        allow_private: bool = False,
        timeout: float = 5.0,
        max_attempts: int = 3,
        backoff: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.webhook_url = webhook_url
        self.request_id = request_id
        self.prompt_version = prompt_version
        self._txns = {t.transaction_id: t for t in transactions}
        self._min_rank = Severity(min_severity).rank
        self._allow_private = allow_private
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self.results: dict[str, NotificationResult] = {}
        self.sent: dict[str, Finding] = {}

    def notify(self, transaction_id: str, severity: str, reason: str) -> NotificationResult | None:
        """Returns None when the finding is below this tenant's alert threshold."""
        if transaction_id not in self._txns:
            raise KeyError(f"unknown transaction_id {transaction_id!r}")
        if transaction_id in self.results:  # dedupe: never notify twice per request
            return self.results[transaction_id]

        sev = Severity(severity)
        if sev.rank < self._min_rank:
            return None
        txn = self._txns[transaction_id]
        finding = Finding(transaction_id=transaction_id, severity=sev, reason=reason[:500])
        # Stable idempotency key so the receiver can drop duplicates on retry.
        idem = hashlib.sha256(f"{self.tenant_id}:{transaction_id}:{self.prompt_version}".encode()).hexdigest()
        payload = {
            "event": "risk_detected",
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "prompt_version": self.prompt_version,
            "severity": sev.value,
            "reason": finding.reason,
            "transaction": txn.model_dump(mode="json"),
        }
        result = self._post(transaction_id, payload, idem)
        self.results[transaction_id] = result
        self.sent[transaction_id] = finding
        return result

    def _post(self, transaction_id: str, payload: dict, idem: str) -> NotificationResult:
        try:
            validate_webhook_url(self.webhook_url, self._allow_private)
        except WebhookURLError as exc:
            log.warning("webhook blocked tenant=%s: %s", self.tenant_id, exc)
            return NotificationResult(
                transaction_id=transaction_id, delivered=False, attempts=0, error=str(exc)
            )

        last_error: str | None = None
        status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = self._client.post(self.webhook_url, json=payload, headers={"Idempotency-Key": idem})
                status = resp.status_code
                if resp.status_code < 300:
                    return NotificationResult(
                        transaction_id=transaction_id,
                        delivered=True,
                        attempts=attempt,
                        status_code=status,
                    )
                last_error = f"http {resp.status_code}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break  # 4xx will not succeed on retry
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            if attempt < self._max_attempts:
                time.sleep(self._backoff * (2 ** (attempt - 1)))
        log.warning("webhook failed tenant=%s txn=%s error=%s", self.tenant_id, transaction_id, last_error)
        return NotificationResult(
            transaction_id=transaction_id,
            delivered=False,
            attempts=attempt,
            status_code=status,
            error=last_error,
        )
