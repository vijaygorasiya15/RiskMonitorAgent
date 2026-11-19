# Risk Monitor Agent

An AI agent that takes a batch of transactions, uses an LLM (AWS Bedrock, via the
[Strands](https://strandsagents.com) framework) to decide which look suspicious, notifies
the risk team through a **per-tenant webhook**, and returns a summary of its findings.

Deployable as a container with Docker Compose (local) or on **Bedrock AgentCore Runtime**.

```
client ──POST /invocations──► agent container (Strands + BedrockAgentCoreApp, :8080)
 {tenant_id, transactions}      1. validate input (pydantic) + authenticate tenant
                                2. LLM reasons over the batch (Bedrock) -> structured findings
                                3. tool notify_risk_team() ──► that tenant's webhook only
                                4. reconcile + return summary JSON
```

## Quick start (free, no AWS needed)

Requires Docker Desktop.

```bash
docker compose up --build            # agent on :8080, mock webhook receiver on :9100
```

Send transactions (demo tenants are defined in `tenants.example.yaml`):

```bash
curl -s localhost:8080/invocations \
  -H 'content-type: application/json' -H 'x-api-key: demo-key-acme' \
  -d @examples/request_acme.json
```

See what the risk team's webhook received, separated per tenant:

```bash
curl -s localhost:9100/received/acme
curl -s localhost:9100/received/globex
```

By default compose runs `LLM_MODE=mock`, a deterministic rule-based engine (large amounts,
night hours, risky merchants, velocity). It exercises everything except the LLM itself.

### Use the real LLM (costs a few rupees per test)

1. AWS account, then **Bedrock → Model access**: enable the model in your region.
2. Copy `.env.example` to `.env`, set `LLM_MODE=bedrock` and your AWS credentials.
3. Confirm `llm.model_id` in `config.yaml` matches a model/inference-profile ID available in your
   console (Bedrock → Model catalog).
4. `docker compose up --build` and run the same curl.

## API

`POST /invocations` (also `GET /ping` for health). Request:

```json
{
  "tenant_id": "acme",
  "prompt_version": "v1",
  "transactions": [
    {"transaction_id": "t-1", "amount": 9800.0, "currency": "USD", "merchant": "QuickCrypto",
     "timestamp": "2026-11-01T02:41:00Z", "customer_id": "cust-2"}
  ]
}
```

`prompt_version` is optional (defaults to the active version in `config.yaml`). Tenant
authentication is the `x-api-key` header. Response:

```json
{
  "status": "ok", "request_id": "…", "tenant_id": "acme", "prompt_version": "v1",
  "engine": "bedrock:<model>", "total_transactions": 6, "flagged_count": 1,
  "findings": [{"transaction_id": "t-1", "severity": "high", "reason": "…"}],
  "notifications": [{"transaction_id": "t-1", "delivered": true, "attempts": 1, "status_code": 200}],
  "summary": "…"
}
```

Errors return `{"status": "error", "error": "<code>", ...}` (`invalid_request`, `unauthorized`,
`unknown_prompt_version`, `too_many_transactions`, `duplicate_transaction_id`, `agent_error`).

## Project layout

| Path | Purpose |
|---|---|
| `app/main.py` | AgentCore entrypoint (`/invocations`, `/ping`) |
| `app/agent.py` | Strands agent (Bedrock) + mock engine; `notify_risk_team` tool |
| `app/service.py` | auth, prompt selection, reconciliation of model output |
| `app/webhook.py` | tenant-bound notifier: SSRF guard, retries, idempotency key |
| `app/tenants.py` | per-tenant webhook URL, API key, alert threshold |
| `prompts/v*.md` | versioned system prompts |
| `config.yaml` | model, region, active prompt version, thresholds |
| `scripts/deploy_agentcore.py` | deploy / promote / rollback on AgentCore |
| `.github/workflows/ci-cd.yml` | pipeline |
| `ANSWERS.md` | written answers (Part 4) |

## Tests

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q          # 30 tests; LLM and webhook are mocked, so no AWS or cost
ruff check . && ruff format --check .
```

## CI/CD pipeline (`.github/workflows/ci-cd.yml`)

```
push to main ─► test (ruff + pytest) ─► build image + container smoke test (mock engine)
             ─► [opt-in] push arm64 image to ECR, tag = git SHA
             ─► new AgentCore runtime version ─► "staging" endpoint
             ─► manual approval ─► "prod" endpoint
```

**How a prompt or config change reaches a running deployment:**

1. Add `prompts/v2.md` (or edit `config.yaml`, e.g. `prompt.active_version: v2`) and open a PR.
2. CI runs lint + tests; merging to `main` triggers the build. The image is tagged with the git SHA,
   so every deployment is immutable and traceable to a commit.
3. The deploy job creates a **new AgentCore runtime version** and points the `staging` endpoint at it.
4. After the approval gate, `prod` is pointed at that exact version. Customers that pinned
   `prompt_version: v1` are unaffected.

**The staging gate.** After deploying to `staging`, the pipeline invokes that deployed endpoint
(`scripts/smoke_agentcore.py`) with a small batch containing one obvious fraud. It fails, and the pipeline
stops before prod is promoted or approval is requested, if: the prompt version is not the one `config.yaml`
asks for, the real LLM is not in use (mock engine), the obvious fraud was not flagged, almost everything
was flagged (false-positive spike), or the webhook did not deliver. These rules are unit-tested
(`tests/test_smoke_validation.py`). It needs a `smoke` tenant in the runtime's `TENANTS_JSON`.

Cloud deploy jobs are **off by default**. They run only when the repo variable
`DEPLOY_ENABLED=true` and the AWS secrets/variables below are set (it creates AWS resources, which costs money):
`AWS_DEPLOY_ROLE_ARN` (OIDC role, no static keys), `AGENTCORE_ROLE_ARN`, vars `ECR_REPOSITORY`,
`AGENTCORE_RUNTIME_ID`, `AWS_REGION`.

### Test the AWS release flow for free (local emulator)

```bash
bash scripts/aws_emulator_e2e.sh
```

This starts [Floci](https://floci.io), a free local AWS emulator ([docker-compose.floci.yml](docker-compose.floci.yml),
host port 4567), then runs the **same** `bootstrap_agentcore.py` / `deploy_agentcore.py` commands the
pipeline uses: create runtime, deploy new versions, promote `staging` to `prod`, roll back. It asserts
that prompt version, image tag, and endpoints end up where expected, then tears everything down. It also runs in CI.

What the emulator **cannot** prove: it runs no real LLM (Bedrock Runtime returns canned replies) and
starts no agent container (AgentCore invoke returns a fixed stub), so the LLM path and the container on
AgentCore still need one real-AWS check. ECR repo creation works, but image push needs the emulator to
control your Docker daemon, so push is only tested against real AWS.

### Switching from the emulator to a real AWS account

Nothing in the code mentions the emulator. Scripts use the standard AWS SDK config, so only the
environment changes:

| | Local emulator | Real AWS |
|---|---|---|
| `AWS_ENDPOINT_URL` | `http://localhost:4567` | **unset** |
| credentials | dummy (`test`/`test`) | your IAM user/role or `AWS_PROFILE` |
| `AGENTCORE_ROLE_ARN` | any ARN | real execution role |
| image registry | n/a | real ECR URI |
| `LLM_MODE` | `mock` | `bedrock` |

`scripts/e2e_deploy_flow.py` refuses to run without `AWS_ENDPOINT_URL` unless you pass `--real`, so
it cannot touch a real account by accident.

### Rollback

```bash
python scripts/deploy_agentcore.py rollback                # prod -> previous version, seconds, no rebuild
python scripts/deploy_agentcore.py rollback --version 3
python scripts/deploy_agentcore.py status
```

Locally: `PROMPT_VERSION=v1 docker compose up -d`.

## Security notes

- **Tenant isolation:** each request resolves exactly one tenant; the webhook client is bound to that
  tenant's URL, so neither the LLM nor the payload can redirect an alert elsewhere. A fresh agent is
  created per request (no shared conversation state).
- **Prompt injection:** transactions are passed as delimited data; the prompt says to ignore
  instructions inside them. The service also drops findings for IDs that were not in the input and
  builds webhook payloads from the validated input, not from model text.
- **Webhook safety:** HTTPS only, resolved IPs must be public (blocks internal/metadata addresses),
  no redirects, timeout, bounded retries with backoff, `Idempotency-Key` header. The local demo
  relaxes the IP rule with `WEBHOOK_ALLOW_PRIVATE=true` because the mock receiver is on a private network.
- **Secrets:** never committed. Use `*_env` keys in `tenants.yaml`, env vars locally, and AWS Secrets
  Manager / OIDC in the cloud. Containers run as a non-root user.

## Status / honesty

Verified locally: unit + API tests (30), lint, and the full request path with the mock engine and
per-tenant webhooks. **Not yet exercised against real AWS:** the Bedrock model call, the ECR push,
and the AgentCore deploy; these need your AWS account and cost a little, so they are run on request.
