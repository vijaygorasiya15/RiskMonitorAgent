# AWS setup for a real deployment

Steps to run the agent on Amazon Bedrock AgentCore. Everything here is optional: the project
runs fully without AWS (mock engine, Docker Compose). Steps marked **costs** create billable
resources (expected: cents), so delete them when finished (see "Cleanup").

Region used throughout: `us-east-1`. Replace `<ACCOUNT_ID>` with your 12-digit account ID.

## 0. One-time account prerequisites
- A budget alert (Billing -> Budgets, about $5/month).
- An IAM user for the CLI (not root) with access keys, configured via `aws configure`.
- Bedrock: submit the Anthropic use-case form (first chat in the Bedrock Playground) and make
  sure the model's token quota is above 0 (Service Quotas -> Amazon Bedrock). A new account can
  start at 0 and needs an increase request. Without this, the real LLM returns `ThrottlingException`.
  The deployment itself does not need this if you deploy with `--llm-mode mock`.

## 1. Permissions for the CLI user (console, as account owner)
The CLI user needs more than model access to deploy. Attach this policy (name it
`RiskMonitorDeploy`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ecr:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/RiskMonitorAgentRuntimeRole",
      "Condition": { "StringEquals": { "iam:PassedToService": "bedrock-agentcore.amazonaws.com" } } },
    { "Effect": "Allow", "Action": "iam:CreateServiceLinkedRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/aws-service-role/*",
      "Condition": { "StringLike": { "iam:AWSServiceName": "*bedrock-agentcore.amazonaws.com" } } }
  ]
}
```

`iam:CreateServiceLinkedRole` is needed once: AgentCore creates its own service-linked role the first time
a runtime is created, and the deploy fails with "Failed creating service linked role" without it.

This is broad on purpose for a throwaway demo account. Narrow it for anything shared.

## 2. Runtime execution role (console)
The role the *running agent* uses. IAM -> Roles -> Create role -> Custom trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": { "StringEquals": { "aws:SourceAccount": "<ACCOUNT_ID>" } }
  }]
}
```

Name it `RiskMonitorAgentRuntimeRole`, and attach this inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken"], "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
      "Resource": "arn:aws:ecr:us-east-1:<ACCOUNT_ID>:repository/risk-monitor" },
    { "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:us-east-1:<ACCOUNT_ID>:log-group:/aws/bedrock-agentcore/*" },
    { "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*" }
  ]
}
```

Check the AgentCore Runtime permissions page in the AWS docs if a deploy fails with an access
error: the exact list can change.

## 3. Create the image repository (**costs**: about $0.10 per GB-month)
```
aws ecr create-repository --repository-name risk-monitor --region us-east-1
```

## 4. Build and push an arm64 image (AgentCore requires ARM64)
```
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker buildx build --platform linux/arm64 -t <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/risk-monitor:sha-$(git rev-parse --short HEAD) --push .
```

## 5. Create the runtime and deploy (**costs**: billed only for active use)
Mock engine first (no Bedrock quota needed):
```
export AGENTCORE_ROLE_ARN=arn:aws:iam::<ACCOUNT_ID>:role/RiskMonitorAgentRuntimeRole
IMAGE=<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/risk-monitor:sha-<TAG>
export AGENTCORE_RUNTIME_ID=$(python scripts/bootstrap_agentcore.py --image "$IMAGE" --llm-mode mock)
python scripts/deploy_agentcore.py deploy --image "$IMAGE" --llm-mode mock   # new version -> "staging"
python scripts/smoke_agentcore.py --endpoint staging --lenient
python scripts/deploy_agentcore.py promote --version <N>                    # "prod" -> that version
python scripts/deploy_agentcore.py rollback                                 # "prod" -> previous version
```

Tenant API keys and webhook URLs: set `TENANTS_JSON` before `bootstrap_agentcore.py`
(webhook URLs must be public HTTPS; the SSRF guard blocks private addresses).

Real LLM later: run the same `deploy` without `--llm-mode` (the default is `bedrock`).
A normal pipeline deploy always uses the default, so it restores the real model.

## 6. A real webhook receiver for demos (Lambda; free tier covers it)
A public HTTPS endpoint you control, so a tenant's alerts can be delivered for real and shown on screen.

1. Role `RiskMonitorWebhookReceiverRole`: trusted entity AWS service -> Lambda, managed policy
   `AWSLambdaBasicExecutionRole`, plus an inline policy allowing `logs:FilterLogEvents` on
   `arn:aws:logs:us-east-1:<ACCOUNT_ID>:log-group:/aws/lambda/risk-monitor-webhook-receiver:*`.
2. Add to `RiskMonitorDeploy`: `lambda:CreateFunction, GetFunction, GetFunctionConfiguration,
   UpdateFunctionCode, UpdateFunctionConfiguration, DeleteFunction, CreateFunctionUrlConfig,
   GetFunctionUrlConfig, DeleteFunctionUrlConfig, AddPermission, GetPolicy` on
   `arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:risk-monitor-webhook-receiver`, and `iam:PassRole` on the
   role above for `lambda.amazonaws.com`.
3. Deploy: `RECEIVER_ROLE_ARN=arn:aws:iam::<ACCOUNT_ID>:role/RiskMonitorWebhookReceiverRole python scripts/deploy_webhook_receiver.py deploy`.
   It prints the webhook URL (function URL + a random secret path). `GET` that URL in a browser to see
   received alerts.
4. Give the URL to a tenant: set it as `webhook_url` in `TENANTS_JSON` and run `deploy_agentcore.py deploy`
   with `TENANTS_JSON` set (it replaces the deployed tenant config).

### Finding the receiver URL again, and showing alerts in a demo
The URL is not stored in git (the repo is public). The secret part lives in AWS, as the Lambda
function's `RECEIVER_SECRET` environment variable, so it can always be printed again:

1. Open a terminal in the project folder.
2. Set the region if your shell doesn't have it: `export AWS_REGION=us-east-1`
   (PowerShell: `$env:AWS_REGION = "us-east-1"`).
3. Run `python scripts/deploy_webhook_receiver.py url`. It is free and changes nothing.
4. Paste the printed URL into a browser. That page lists the alerts received in the last 24 hours.
5. Trigger an alert by calling the deployed agent (for example `python scripts/smoke_agentcore.py
   --endpoint staging --lenient`), wait a few seconds, then refresh the page. Log delivery can lag.
6. Tip: bookmark the URL in your browser for the demo instead of saving it in a file.

If the URL ever leaks: `python scripts/deploy_webhook_receiver.py delete`, then `deploy` again. That creates
a new function with a new secret path (then update the tenant's `webhook_url` and redeploy the runtime).

## Cleanup (stops all charges)
Delete the runtime's endpoints first if the delete is refused, then:
```
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <ID> --region us-east-1
python scripts/deploy_webhook_receiver.py delete
aws ecr delete-repository --repository-name risk-monitor --force --region us-east-1
```
Then delete the IAM roles and policies if you no longer need them.
