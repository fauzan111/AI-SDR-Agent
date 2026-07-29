"""Async tasks. Kept thin: the pipeline lives in app/, the worker just runs it
off the request path. Retry-safe: the task re-reads the lead and no-ops if it's
already terminal, so a redelivered message never double-processes."""
from __future__ import annotations

from app.db.base import SessionLocal
from app.pipeline import process_lead
from app.runtime import get_calendar_singleton, get_crm, get_enricher_singleton, get_llm_or_none
from workers.celery_app import celery_app


@celery_app.task(name="workers.ping")
def ping() -> str:
    """Trivial task to verify broker connectivity in CI / smoke tests."""
    return "pong"


@celery_app.task(
    name="workers.process_lead",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,  # redeliver on worker crash; pipeline is idempotent so it's safe
)
def process_lead_task(self, *, lead_id: str, tenant_id: str) -> str:
    """Tenant-scoped background processing of one lead."""
    db = SessionLocal()
    try:
        process_lead(
            db,
            lead_id=lead_id,
            tenant_id=tenant_id,
            llm=get_llm_or_none(),
            crm=get_crm(),
            enricher=get_enricher_singleton(),
            calendar=get_calendar_singleton(),
        )
        return "ok"
    except Exception as exc:  # noqa: BLE001 - CRM/network failures are retryable
        raise self.retry(exc=exc) from exc
    finally:
        db.close()
