"""Agent engines. Both expose `assess(transactions) -> RiskAssessment` and call the
tenant-bound Notifier when they detect risk.

- StrandsRiskAgent: real LLM reasoning on AWS Bedrock (Strands framework).
- MockRiskAgent: deterministic rule-based engine (free; for local demo and tests).
"""

from __future__ import annotations

import json
from typing import Protocol

from .config import Settings
from .heuristic import evaluate
from .models import RiskAssessment, Transaction
from .webhook import Notifier


class RiskAgent(Protocol):
    engine: str

    async def assess(self, transactions: list[Transaction]) -> RiskAssessment: ...


def build_user_prompt(transactions: list[Transaction]) -> str:
    """Transactions are passed as DATA inside a delimited JSON block. The system
    prompt tells the model to never follow instructions found inside it."""
    data = [t.model_dump(mode="json") for t in transactions]
    return (
        "Analyze the transactions below. For every suspicious one, call the "
        "notify_risk_team tool, then return your structured assessment.\n\n"
        "<transactions>\n" + json.dumps(data, indent=1) + "\n</transactions>"
    )


class MockRiskAgent:
    engine = "mock-rules"

    def __init__(self, settings: Settings, notifier: Notifier) -> None:
        self._s = settings
        self._notifier = notifier

    async def assess(self, transactions: list[Transaction]) -> RiskAssessment:
        import asyncio

        result = evaluate(transactions, self._s)
        for f in result.findings:
            await asyncio.to_thread(self._notifier.notify, f.transaction_id, f.severity.value, f.reason)
        return result


class StrandsRiskAgent:
    def __init__(self, settings: Settings, notifier: Notifier, system_prompt: str) -> None:
        self._s = settings
        self._notifier = notifier
        self._system_prompt = system_prompt
        self.engine = f"bedrock:{settings.model_id}"

    def _build(self):
        # Imported lazily so unit tests / mock mode never need AWS libraries loaded.
        from strands import Agent, tool
        from strands.models import BedrockModel

        notifier = self._notifier

        @tool
        def notify_risk_team(transaction_id: str, severity: str, reason: str) -> str:
            """Notify the risk team about ONE suspicious transaction.

            Args:
                transaction_id: The transaction_id exactly as given in the input.
                severity: One of "low", "medium", "high".
                reason: One short sentence explaining why it is suspicious.
            """
            try:
                res = notifier.notify(transaction_id, severity, reason)
            except (KeyError, ValueError) as exc:
                return f"rejected: {exc}"
            if res is None:
                return "recorded; below this tenant's alert threshold, no notification sent"
            return "delivered" if res.delivered else f"delivery failed: {res.error}"

        model = BedrockModel(
            model_id=self._s.model_id,
            region_name=self._s.region,
            temperature=self._s.temperature,
        )
        return Agent(
            model=model,
            system_prompt=self._system_prompt,
            tools=[notify_risk_team],
            structured_output_model=RiskAssessment,
            callback_handler=None,
        )

    async def assess(self, transactions: list[Transaction]) -> RiskAssessment:
        agent = self._build()  # fresh agent per request: no state shared across tenants
        result = await agent.invoke_async(build_user_prompt(transactions))
        if result.structured_output is None:
            raise RuntimeError("model returned no structured output")
        return result.structured_output


def build_agent(settings: Settings, notifier: Notifier, system_prompt: str) -> RiskAgent:
    if settings.llm_mode == "mock":
        return MockRiskAgent(settings, notifier)
    if settings.llm_mode == "bedrock":
        return StrandsRiskAgent(settings, notifier, system_prompt)
    raise ValueError(f"unknown LLM_MODE {settings.llm_mode!r}")
