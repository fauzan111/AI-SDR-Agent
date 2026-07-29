"""Application settings, loaded from environment. Single source of truth."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "sdr-agent"
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # --- Database (multi-tenant; every row is tenant-scoped) ---
    database_url: str = "postgresql+psycopg2://agent:agent@localhost:5432/agentdb"

    # --- Async workers ---
    redis_url: str = "redis://localhost:6379/0"
    # When true, the webhook processes the lead synchronously instead of
    # enqueuing to Celery. Lets the app run (and demo) with no Redis/worker.
    # Set false in a full deployment where a Celery worker consumes the queue.
    process_inline: bool = True

    # --- LLM (provider-agnostic client) ---
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_model: str = "claude-opus-4-8"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # --- CRM (adapter pattern; built against HubSpot's API shape) ---
    # "mock" works offline; "hubspot" uses the real API when a token is set.
    crm_provider: Literal["mock", "hubspot"] = "mock"
    hubspot_access_token: str = ""

    # --- Qualification defaults (per-tenant ICP overrides these) ---
    default_qualification_threshold: int = 60  # 0-100 combined score to qualify

    # --- Auth ---
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"


@lru_cache
def get_settings() -> Settings:
    return Settings()
