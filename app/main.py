"""Entrypoint. BedrockAgentCoreApp serves POST /invocations and GET /ping on :8080,
the AgentCore container contract, so the same image runs in docker-compose and in
AgentCore Runtime."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import ValidationError

from .config import Settings, load_settings
from .models import AnalyzeRequest
from .service import RequestError, analyze
from .tenants import Tenant, load_configured_tenants

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("risk_monitor")

app = BedrockAgentCoreApp()


@lru_cache(maxsize=1)
def _state() -> tuple[Settings, dict[str, Tenant]]:
    settings = load_settings()
    return settings, load_configured_tenants(settings.tenants_file)


# AgentCore only forwards allow-listed headers, and custom ones must carry this prefix.
_API_KEY_HEADERS = {"x-api-key", "x-amzn-bedrock-agentcore-runtime-custom-api-key"}


def _api_key(context: Any) -> str | None:
    headers = getattr(context, "request_headers", None) or {}
    for name, value in headers.items():
        if name.lower() in _API_KEY_HEADERS:
            return value
    return None


@app.entrypoint
async def invoke(payload: dict, context=None) -> dict:
    settings, tenants = _state()
    try:
        request = AnalyzeRequest.model_validate(payload)
    except ValidationError as exc:
        errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return {"status": "error", "error": "invalid_request", "details": errors[:20]}

    try:
        response = await analyze(request, settings=settings, tenants=tenants, api_key=_api_key(context))
    except RequestError as exc:
        return {"status": "error", "error": exc.code, "message": exc.message}
    return response.model_dump(mode="json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
