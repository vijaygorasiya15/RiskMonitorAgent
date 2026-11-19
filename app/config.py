"""Settings: config.yaml (versioned in git) + environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    llm_mode: str  # "bedrock" (real LLM) or "mock" (rule-based, free, no AWS)
    model_id: str
    region: str
    temperature: float
    active_prompt_version: str
    prompts_dir: Path
    max_transactions: int
    webhook_timeout_seconds: float
    webhook_max_attempts: int
    webhook_backoff_seconds: float
    webhook_allow_private: bool  # only true for local demo (mock receiver)
    tenants_file: Path
    # thresholds used by the rule-based signals / mock engine
    large_amount: float
    night_start_hour: int
    night_end_hour: int
    velocity_count: int
    velocity_window_minutes: int
    risky_merchant_keywords: tuple[str, ...]


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(config_path: Path | None = None) -> Settings:
    path = Path(os.getenv("CONFIG_FILE", config_path or ROOT / "config.yaml"))
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm = cfg.get("llm", {})
    wh = cfg.get("webhook", {})
    rules = cfg.get("rules", {})

    tenants_file = Path(os.getenv("TENANTS_FILE", ROOT / "tenants.yaml"))
    if not tenants_file.exists():
        tenants_file = ROOT / "tenants.example.yaml"

    return Settings(
        llm_mode=os.getenv("LLM_MODE", llm.get("mode", "bedrock")).lower(),
        model_id=os.getenv("BEDROCK_MODEL_ID", llm["model_id"]),
        region=os.getenv("AWS_REGION", llm.get("region", "us-east-1")),
        temperature=float(llm.get("temperature", 0.0)),
        active_prompt_version=os.getenv("PROMPT_VERSION", cfg["prompt"]["active_version"]),
        prompts_dir=ROOT / cfg["prompt"].get("dir", "prompts"),
        max_transactions=int(cfg.get("max_transactions", 200)),
        webhook_timeout_seconds=float(wh.get("timeout_seconds", 5)),
        webhook_max_attempts=int(wh.get("max_attempts", 3)),
        webhook_backoff_seconds=float(wh.get("backoff_seconds", 0.5)),
        webhook_allow_private=_bool(os.getenv("WEBHOOK_ALLOW_PRIVATE"), False),
        tenants_file=tenants_file,
        large_amount=float(rules.get("large_amount", 5000)),
        night_start_hour=int(rules.get("night_start_hour", 0)),
        night_end_hour=int(rules.get("night_end_hour", 5)),
        velocity_count=int(rules.get("velocity_count", 3)),
        velocity_window_minutes=int(rules.get("velocity_window_minutes", 10)),
        risky_merchant_keywords=tuple(
            k.lower()
            for k in rules.get(
                "risky_merchant_keywords",
                ["crypto", "gift card", "wire transfer", "casino", "gambling"],
            )
        ),
    )
