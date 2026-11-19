# Written answers

## 1. You update the agent's prompt logic. How do you ship it without breaking existing users? What does versioning look like?

**Treat the prompt as a versioned artifact, not a hot-edited string.**

- Prompts live in git as `prompts/v1.md`, `prompts/v2.md`. Files are never edited after release;
  a change is a new version. `config.yaml` names the `active_version` default.
- Callers may pin `prompt_version` in the request. Customers who pinned `v1` keep getting `v1` behavior
  until they choose to move. Every response and every webhook payload records the `prompt_version`
  that produced it, so any alert can be traced back to the exact logic.
- Code is versioned by image tag = git SHA. On AgentCore each deploy creates an immutable
  **runtime version**, and traffic reaches it through a named **endpoint** (`staging`, `prod`).
  An endpoint is just a pointer to a version.

**Release process (safest first):**
1. Run the new prompt against a labeled evaluation set of transactions in CI; compare precision/recall
   and false-positive rate against the current version. Block the merge if it regresses.
2. Deploy to `staging`; run the smoke test.
3. **Shadow mode:** run v2 on a copy of real traffic, store its findings, and send *no* webhooks. Compare
   with v1.
4. **Canary:** move a small share of traffic (or one friendly tenant) to v2, watching flag rate,
   webhook volume, and error rate.
5. Promote `prod` to v2; keep v1 deployed for rollback.

Schema/API changes follow the same rule: add fields, don't change or remove them, so older clients keep working.

## 2. The new version starts producing false positives. How do you roll back quickly?

Because releases are immutable and traffic goes through an endpoint pointer, rollback is a pointer move,
not a rebuild:

```bash
python scripts/deploy_agentcore.py rollback          # prod endpoint -> previous runtime version
```

It takes seconds. For prompt-only regressions there are even faster options: set `PROMPT_VERSION=v1`
(or the config's `active_version`), or tell affected tenants to pin `prompt_version: v1`.

To make it **quick to notice**, alert on the flag rate per tenant versus a baseline (a sudden jump
in flagged-per-transaction or webhook volume pages someone), and consider an automatic rollback when it
crosses a hard limit. Afterwards, add the false-positive cases to the evaluation set so the regression can't
return, and fix forward as `v3`. Do not edit `v2`.

Webhook receivers can dedupe and trace by the `Idempotency-Key` and `prompt_version` fields, so
alerts raised by the bad version are easy to identify and dismiss.

## 3. Two customers use this agent. How do you keep their data, webhook URLs, and results completely separate?

Defense in depth, at each layer:

- **Identity:** every request is authenticated per tenant (API key here; in production a JWT/IAM identity
  from the gateway), and the tenant is derived from that identity, never trusted from the payload alone.
  Unknown tenant and bad key return the same error so tenant existence isn't leaked.
- **Config and secrets:** each tenant has its own webhook URL, alert threshold, and credentials, loaded
  from per-tenant entries in Secrets Manager (referenced with `*_env` keys, never committed).
- **Execution:** the webhook client is constructed per request and bound to that tenant's URL, so the model
  cannot choose a destination. A new agent instance per request means no conversation memory, cache, or tool
  state is shared. On AgentCore, each session also runs in its own isolated microVM.
- **Outputs:** the response and webhook contain only that tenant's transactions. Webhook payloads are built
  from validated input, not model text. Logs carry tenant and transaction IDs but no raw customer data, and
  any storage (audit log, evaluation data) is keyed/partitioned by `tenant_id`.
- **Network safety:** webhook URLs are validated (HTTPS, public IPs only, no redirects) so a tenant can't
  aim the agent at internal services (SSRF) or at another tenant.
- **Stronger isolation if needed:** a separate AgentCore runtime + IAM role + KMS key per tenant (or per tier)
  gives blast-radius isolation at the infrastructure level, at higher cost.

This is exercised by tests: `test_tenants_are_isolated` (alerts hit only the right URL),
`test_tenant_threshold_respected`, `test_wrong_key_and_unknown_tenant_same_error`.
