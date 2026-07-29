"""Test configuration. Point the app at a SQLite DB and dummy secret *before*
any app module (and its cached settings) is imported, so tests run with no
external Postgres/Redis/LLM/CRM dependencies.

No provider keys and CRM_PROVIDER=mock: qualification uses the offline heuristic
fit and the CRM push uses the deterministic mock adapter. Zero network calls.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./test_sdr.sqlite")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("CRM_PROVIDER", "mock")
os.environ.setdefault("PROCESS_INLINE", "true")
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
