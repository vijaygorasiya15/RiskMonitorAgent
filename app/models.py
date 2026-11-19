"""Request / response schemas. All external input is validated here."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"

    @property
    def rank(self) -> int:
        return {"low": 1, "medium": 2, "high": 3}[self.value]


class Transaction(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, lt=1e12)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    merchant: str = Field(min_length=1, max_length=120)
    timestamp: datetime
    customer_id: str = Field(min_length=1, max_length=64)

    @field_validator("merchant", "transaction_id", "customer_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class AnalyzeRequest(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    transactions: list[Transaction] = Field(min_length=1)
    # Optional: pin a prompt version (e.g. "v1"). Defaults to the active one.
    prompt_version: str | None = Field(default=None, pattern=r"^v\d+(\.\d+)*$")


class Finding(BaseModel):
    """One suspicious transaction. This is also the LLM's structured output."""

    transaction_id: str
    severity: Severity
    reason: str = Field(max_length=500)


class RiskAssessment(BaseModel):
    """Structured output the LLM must return."""

    findings: list[Finding] = Field(default_factory=list)
    summary: str = Field(default="", max_length=1500)


class NotificationResult(BaseModel):
    transaction_id: str
    delivered: bool
    attempts: int
    status_code: int | None = None
    error: str | None = None


class AnalyzeResponse(BaseModel):
    status: str = "ok"
    request_id: str
    tenant_id: str
    prompt_version: str
    engine: str
    total_transactions: int
    flagged_count: int
    findings: list[Finding]
    notifications: list[NotificationResult]
    summary: str
