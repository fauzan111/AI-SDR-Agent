"""Process-wide singletons: LLM client and the CRM / enrichment / calendar
adapters. Everything is swappable via env; defaults are offline-friendly."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.crm.adapter import CRMAdapter, get_crm_adapter
from app.enrichment.enricher import Enricher, get_enricher
from app.llm.client import LLMClient
from app.scheduling.calendar import CalendarAdapter, get_calendar


@lru_cache
def get_llm_or_none() -> LLMClient | None:
    """LLM client if a provider key is set, else None (offline heuristic fit)."""
    s = get_settings()
    has_key = (s.llm_provider == "anthropic" and s.anthropic_api_key) or (
        s.llm_provider == "openai" and s.openai_api_key
    )
    return LLMClient() if has_key else None


@lru_cache
def get_crm() -> CRMAdapter:
    s = get_settings()
    return get_crm_adapter(s.crm_provider, s.hubspot_access_token)


@lru_cache
def get_enricher_singleton() -> Enricher:
    return get_enricher()


@lru_cache
def get_calendar_singleton() -> CalendarAdapter:
    return get_calendar()
