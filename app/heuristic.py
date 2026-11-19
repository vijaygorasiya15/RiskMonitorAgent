"""Rule-based signals. Used by the free 'mock' engine (LLM_MODE=mock) so the whole
stack can be demoed and tested without AWS or any cost."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .config import Settings
from .models import Finding, RiskAssessment, Severity, Transaction


def evaluate(transactions: list[Transaction], s: Settings) -> RiskAssessment:
    reasons: dict[str, list[str]] = defaultdict(list)

    for t in transactions:
        if t.amount >= s.large_amount:
            reasons[t.transaction_id].append(f"large amount {t.amount:,.2f} {t.currency}")
        if s.night_start_hour <= t.timestamp.hour < s.night_end_hour:
            reasons[t.transaction_id].append(f"unusual hour ({t.timestamp.hour:02d}:00)")
        merchant = t.merchant.lower()
        if any(k in merchant for k in s.risky_merchant_keywords):
            reasons[t.transaction_id].append(f"high-risk merchant '{t.merchant}'")

    # velocity: many transactions by the same customer in a short window
    by_customer: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        by_customer[t.customer_id].append(t)
    window = timedelta(minutes=s.velocity_window_minutes)
    for customer, txns in by_customer.items():
        txns.sort(key=lambda x: x.timestamp)
        for i, t in enumerate(txns):
            burst = [u for u in txns[i:] if u.timestamp - t.timestamp <= window]
            if len(burst) >= s.velocity_count:
                for u in burst:
                    msg = f"velocity: {len(burst)} txns by {customer} within {s.velocity_window_minutes} min"
                    if msg not in reasons[u.transaction_id]:
                        reasons[u.transaction_id].append(msg)

    findings = []
    for t in transactions:
        rs = reasons.get(t.transaction_id)
        if not rs:
            continue
        sev = Severity.high if len(rs) >= 2 else Severity.medium
        findings.append(Finding(transaction_id=t.transaction_id, severity=sev, reason="; ".join(rs)[:500]))

    summary = (
        f"Rule-based engine flagged {len(findings)} of {len(transactions)} transactions."
        if findings
        else f"No suspicious activity found in {len(transactions)} transactions."
    )
    return RiskAssessment(findings=findings, summary=summary)
