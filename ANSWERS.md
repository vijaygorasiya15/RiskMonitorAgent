# Written Answers

Short answers to the assessment questions. Where something is already built and tested in this
repo, I say so. Where it is how I would do it in production, I say that too.

---

## How does a prompt or config change get from a code push to a running deployment?

**The idea:** a prompt change is treated like any other code change. Prompts live in `prompts/` and
settings live in `config.yaml`, so every change is reviewed in git and can be traced to a commit.

```
 You push to main
        │
        ▼
 1. Test        Lint + unit tests (LLM and webhook are mocked, so no AWS needed).
        │       A failure stops everything here.
        ▼
 2. Build       Build the container image, tag it with the git commit, smoke-test it.
        │
        ▼
 3. Staging     Create a NEW runtime version with the prompt version from config.yaml,
        │       and point the "staging" endpoint at it.
        ▼
 4. Smoke test  Call the deployed staging endpoint with a small batch that has one obvious
        │       fraud. Fail if the prompt version is wrong, the fraud isn't flagged, almost
        │       everything is flagged, or the webhook doesn't deliver.
        ▼
 5. Approval    A person approves in GitHub.
        │
        ▼
 6. Production  Point the "prod" endpoint at the exact version that passed staging.
```

Every response and every webhook alert includes the `prompt_version` that produced it, so you can always
tell which version made a decision.

*Where to see it:* `.github/workflows/ci-cd.yml`. The README has a step-by-step example (switching from
prompt `v1` to `v2`).

---

## 1. You update the prompt. How do you ship it without breaking existing users? What does versioning look like?

**Short answer:** never edit a released prompt. Add a new version, test it, release it gradually, and let
customers move over when they are ready.

**How versioning works**

| What | How it is versioned |
|---|---|
| Prompts | Separate files: `v1.md`, `v2.md`. A change is always a *new* file. |
| Code | Each build is a container image tagged with the git commit. |
| Deployment | Each deploy is a numbered, unchangeable runtime version. Customers reach it through a named endpoint (`staging`, `prod`) that points at one version. |

**How existing users stay safe**
- A customer can **pin** a version by sending `"prompt_version": "v1"` with their request. They keep getting
  `v1` until they choose to switch. Customers who don't pin get the current default.
- A new version goes to **staging first** and must pass the smoke test before anyone is asked to approve it.
- Request and response formats only ever *add* fields, so older clients keep working.

**What I would add in production** (described, not built in this repo): measure the new prompt against a set of
labelled transactions before release, run it silently alongside the old one ("shadow mode") to compare
results, then roll it out to one friendly customer before everyone.

---

## 2. The new version starts producing false positives. How do you roll back quickly?

**Short answer:** move the `prod` pointer back to the previous version. It takes seconds and needs no rebuild.

Because every release is saved as an unchangeable version, and customers reach it through an endpoint
(a pointer), rolling back just means pointing the endpoint at the older version:

```
python scripts/deploy_agentcore.py rollback
```

- The old version is still deployed, so there is nothing to rebuild or wait for.
- A customer who is badly affected can also pin `prompt_version: v1` straight away.

**Noticing it quickly:** watch how often each customer's transactions get flagged. A sudden jump compared
with normal is the warning sign (an alert on this is what I would add in production).

**Afterwards:** add the false-positive examples to the test set so it can't happen again, and fix it as a *new*
version (`v3`). Never edit `v2`.

*Tested:* on real AWS AgentCore, a new version was deployed, promoted to `prod`, then rolled back. The
endpoint served prompt `v1` again afterwards.

---

## 3. Two customers use this agent. How do you keep their data, webhook URLs and results completely separate?

**Short answer:** each request is tied to exactly one customer from the moment it arrives, and nothing is
shared between customers along the way.

| Concern | What keeps it separate |
|---|---|
| **Who is calling** | Every request is checked against that customer's own API key. An unknown customer and a wrong key give the *same* error, so nobody can discover which customers exist. |
| **Webhook URLs** | Each customer has their own URL in their own config. The code that sends alerts is created per request and locked to that customer's URL, so the AI model cannot choose where an alert goes. |
| **Results** | Responses and alerts only contain that customer's transactions. Alert contents come from the validated input, not from text the model wrote. |
| **State** | A new agent is created for every request, so there is no shared memory or conversation history. |
| **Logs** | Logs record IDs only, not raw customer data. |
| **Alert rules** | Each customer has their own alert threshold (for example, one gets medium and above, another only high). |
| **Safety** | Webhook URLs must be HTTPS and point to the public internet. Internal addresses are blocked, so one customer can't use the agent to reach internal systems. |

**If stricter isolation is needed:** give each customer their own deployment and access role. That is the
strongest option, at a higher cost.

*Tested:* `test_tenants_are_isolated` (alerts only reach the right URL), `test_tenant_threshold_respected`,
and `test_wrong_key_and_unknown_tenant_same_error`.
