# Risk Monitor Agent: Project Plan

Goal: build, deploy, and secure a Risk Monitor agent (AWS Bedrock + Strands + AgentCore-compatible container), delivered within 2 days.

## Ground rules
- **Cost rule:** Nothing that costs money (real Bedrock calls, any AWS resource, ECR push, AgentCore deploy) happens without explicit approval first. Each ask states the action, rough cost, and how to undo it.
- All development and tests run free: the LLM and webhook are mocked.
- Only synthetic transaction data. No real customer data. No secrets in git.

## Deliverables (what the client asked for)
1. Working agent code (Part 1)
2. Dockerfile + docker-compose, reachable via an API endpoint (Part 2)
3. CI/CD pipeline config (Part 3)
4. Short written answers (Part 4)
5. README with setup steps
6. Optional stretch: AgentCore deployment

## Architecture
```
POST /invocations --> Agent container (Strands + BedrockAgentCoreApp, port 8080)
 {tenant_id,            1. Validate input (pydantic, size limits)
  transactions[]}       2. Optional rule-based signals (large amount, odd hour, velocity)
                        3. LLM (Bedrock) reasons over the batch -> structured JSON
                        4. Tool notify_risk_team() --> per-tenant webhook
                        5. Return summary: flagged txns, reasons, severity, webhook status
GET /ping --> health check
```
- `BedrockAgentCoreApp` exposes `/invocations` and `/ping`, the AgentCore contract. The same image runs in Docker Compose and in AgentCore with no code change.
- Transaction input: `transaction_id, amount, merchant, timestamp, customer_id`.

## Project structure (planned)
```
RiskMonitor/
  app/
    main.py            # entrypoint (BedrockAgentCoreApp)
    agent.py           # Strands agent, model config
    tools.py           # notify_risk_team webhook tool
    models.py          # pydantic request/response schemas
    tenants.py         # per-tenant config resolution
    config.py          # loads config.yaml + env
  prompts/
    v1.md              # versioned system prompts
  config.yaml          # model id, region, thresholds, active prompt version
  tenants.example.yaml # tenant -> webhook URL, thresholds (example only)
  tests/               # pytest; Bedrock + webhook mocked
  mock_webhook/        # tiny receiver container for demo
  Dockerfile
  docker-compose.yml
  .github/workflows/ci-cd.yml
  .env.example
  .gitignore
  README.md
  ANSWERS.md           # Part 4 written answers
```

## Phases

| Done | # | Phase | Cost | Output |
|---|---|---|---|---|
| [x] | 1 | Scaffold + schemas + config + tenants | free | project skeleton |
| [x] | 2 | Agent + webhook tool + prompt v1 (+ v2 draft) | free | Part 1 code |
| [x] | 3 | Unit tests (mocked LLM/webhook): 30 passing, lint clean | free | passing pytest |
| [x] | 4 | Dockerfile + compose + mock webhook | free | Part 2. Built and run in Docker: healthy, non-root, smoke test passes, per-tenant webhooks verified. |
| [x] | 5 | GitHub Actions pipeline + AgentCore deploy/promote/rollback script | free | Part 3 (cloud jobs opt-in; not run) |
| [x] | 6 | README + written answers (`README.md`, `ANSWERS.md`) | free | Part 4 |
| [ ] | 7 | AWS setup (you, guided): account, budget alert, IAM, Bedrock access | ~free | credentials ready |
| [ ] | 8 | First real Bedrock test: ASK FIRST | few rupees | end-to-end proof |
| [ ] | 9 | AgentCore deploy (optional): ASK FIRST | small | public endpoint |
| [ ] | 10 | Cleanup + final review | saves money | delete cloud resources |

Legend: [x] done, [~] partly done, [ ] not started.

### Implementation checklist (what is built)
- [x] Input validation (amount, merchant, timestamp, customer ID) with limits
- [x] LLM reasoning via Strands + Bedrock, structured output (`RiskAssessment`)
- [x] `notify_risk_team` tool, webhook bound per tenant
- [x] Webhook: SSRF guard, timeout, retries/backoff, idempotency key, dedupe
- [x] Reconciliation of model output (drop hallucinated IDs, guarantee notification)
- [x] Per-tenant config, API keys, alert thresholds; isolation tests
- [x] Prompt versioning (`prompts/v1.md`, `v2.md`) + request `prompt_version` pin
- [x] Free mock engine (`LLM_MODE=mock`) so everything demos at no cost
- [x] Non-root Dockerfile (AgentCore contract: `/invocations`, `/ping`, :8080)
- [x] docker-compose with mock webhook receiver
- [x] CI: lint, tests, container smoke test; opt-in ECR + AgentCore deploy; manual prod approval
- [x] Rollback script (repoint endpoint to previous version)
- [x] Free AWS emulator (Floci) test of ECR repo + AgentCore bootstrap/deploy/promote/rollback: 9 checks pass, runs in CI
- [x] Staging smoke-test gate before prod promotion (strict rules unit-tested; lenient form runs in emulator e2e)
- [x] `config.yaml` active prompt version now flows into the deployed runtime (`PROMPT_VERSION`)
- [x] Cloud tenant config via `TENANTS_JSON` env var (file still used locally)
- [x] Emulator is swappable for real AWS: only env vars change (`AWS_ENDPOINT_URL` unset, real creds/role)
- [x] Docker image built and run end to end
- [ ] Real Bedrock call verified (structured output + tool use together)
- [ ] AgentCore deployment verified

## Important points to take care of

**Agent quality**
- Force structured JSON output so results are parseable and testable.
- Low temperature for consistent results. Handle LLM errors with a clear response, not a crash.
- Cap batch size and field lengths.

**Security**
- Prompt injection: treat transaction fields (e.g. merchant name) as data, never instructions.
- Webhook SSRF protection: HTTPS only, block private/internal/metadata IPs.
- Webhook reliability: timeout, retries with backoff, idempotency key; report failure in the response.
- Secrets via env vars locally; GitHub OIDC + Secrets Manager in AWS; never in git.
- No PII in logs (log IDs only).
- Container: non-root user, minimal base image, pinned dependencies.

**Multi-tenancy (Part 4, Q3)**
- Every request needs `tenant_id`, resolved to a separate config (webhook URL, thresholds).
- No shared memory, cache, or prompt across tenants. Namespace logs and storage per tenant.
- Stronger option: separate AgentCore runtimes/IAM roles per tenant.

**Versioning and rollback (Part 4, Q1/Q2)**
- Prompts are versioned files (`v1`, `v2`), requests can pin `prompt_version`, default to a stable alias.
- Immutable images tagged by git SHA. Rollout: eval set, then shadow mode, then canary, then full.
- Rollback = repoint the stable alias/endpoint to the previous version (seconds, no rebuild).

**Pipeline (Part 3)**
- Stages: lint, test, build image, push to registry, deploy, smoke test.
- AWS auth through GitHub OIDC (no long-lived keys).
- Demo flow: edit `prompts/` or `config.yaml`, push, pipeline builds a new SHA-tagged image, deploys, smoke-tests.
- CI must not need AWS credentials for tests (everything mocked).

**Cost control**
- Use a small, cheap Bedrock model (configurable in `config.yaml`).
- Budget alert of Rs 300-500 before any AWS use.
- Delete AgentCore runtime and ECR images after the client reviews.

## Your AWS setup checklist (Phase 7, guided step by step later)
1. Create AWS account (card verification charge of about Rs 2 is refunded).
2. Billing, then Budgets: create a Rs 300-500 alert.
3. Create an IAM user/role with only the permissions needed (not root).
4. Bedrock console: request model access in your chosen region.
5. Put credentials in `.env` (git-ignored).

## Open questions
- Which Bedrock model and region will you have access to? (configurable, so this doesn't block coding)
- Is a public HTTPS endpoint required, or is Docker Compose enough? (ask the client if unsure)

## Time estimate
- Code and docs (phases 1-6): ~3 hours
- AWS setup, real test, optional AgentCore (phases 7-10): ~2-4 hours, mostly your AWS steps
- Total: ~5-7 hours, inside the 2-day window
