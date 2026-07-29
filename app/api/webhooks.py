"""Inbound lead webhook — idempotent by construction.

Dedupe strategy (defense in depth):
1. Fast path: look up an existing IdempotencyRecord for (tenant, key); if found,
   return the existing lead without creating anything.
2. Race path: the DB UNIQUE(tenant_id, idempotency_key) constraint guarantees
   that two concurrent requests with the same key cannot both insert. The loser
   catches IntegrityError, rolls back (dropping its orphan lead in the same
   transaction), and returns the winner's lead.

So a retried webhook — even fired concurrently — yields exactly one lead.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.tenant import TenantContext, get_tenant_context
from app.config import get_settings
from app.db.base import get_db
from app.db.models import IdempotencyRecord, Lead, LeadStatus
from app.pipeline import process_lead
from app.runtime import get_calendar_singleton, get_crm, get_enricher_singleton, get_llm_or_none
from app.schemas import LeadIn, LeadOut

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# A lead in one of these states has finished processing; a retry is a pure
# dedupe. Anything else (RECEIVED / FAILED / PUSHED_TO_CRM) is resumable.
_DONE = {LeadStatus.DISQUALIFIED, LeadStatus.SCHEDULED}


def _normalize_key(key: str) -> str:
    """Keys are stored in a bounded column. Hash anything oversized so a huge
    client-supplied Idempotency-Key can't raise a DB error (or a 500)."""
    return key if len(key) <= 200 else "sha256:" + hashlib.sha256(key.encode()).hexdigest()


def to_lead_out(lead: Lead, *, deduplicated: bool = False) -> LeadOut:
    return LeadOut(
        id=lead.id, external_id=lead.external_id, email=lead.email, company=lead.company,
        company_size=lead.company_size, industry=lead.industry, status=lead.status.value,
        score=lead.score, notes=lead.notes, next_action=lead.next_action, crm_id=lead.crm_id,
        meeting_link=lead.meeting_link, deduplicated=deduplicated,
    )


def _existing_lead(
    db: Session, *, tenant_id: str, key: str, external_id: str | None = None
) -> Lead | None:
    """Find an already-created lead by its idempotency key, or (backstop) by its
    external_id — so an inconsistent key source can't slip a duplicate through."""
    rec = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.idempotency_key == key)
        .one_or_none()
    )
    if rec:
        return db.get(Lead, rec.lead_id)
    if external_id is not None:
        return (
            db.query(Lead)
            .filter(Lead.tenant_id == tenant_id, Lead.external_id == external_id)
            .one_or_none()
        )
    return None


def _run_processing(db: Session, *, lead_id: str, tenant_id: str) -> None:
    """Process inline (offline/demo) or hand to Celery (retry-safe background).
    The pipeline is idempotent, so calling this on an in-flight lead is safe."""
    if get_settings().process_inline:
        process_lead(
            db, lead_id=lead_id, tenant_id=tenant_id, llm=get_llm_or_none(),
            crm=get_crm(), enricher=get_enricher_singleton(), calendar=get_calendar_singleton(),
        )
    else:
        from workers.tasks import process_lead_task

        process_lead_task.delay(lead_id=lead_id, tenant_id=tenant_id)


@router.post("/leads", response_model=LeadOut)
def receive_lead(
    body: LeadIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> LeadOut:
    key = _normalize_key(idempotency_key or body.external_id)  # header wins; else external id

    # 1. Fast path: already seen this lead.
    existing = _existing_lead(db, tenant_id=ctx.tenant_id, key=key, external_id=body.external_id)
    if existing is not None:
        # Terminal -> pure dedupe. Non-terminal (RECEIVED/FAILED/PUSHED_TO_CRM)
        # -> a retry should RESUME processing, not silently return a stuck lead.
        if existing.status not in _DONE:
            _run_processing(db, lead_id=existing.id, tenant_id=ctx.tenant_id)
            db.refresh(existing)
        return to_lead_out(existing, deduplicated=True)

    # 2. Insert lead + idempotency record atomically; the UNIQUE constraints
    #    (idempotency key, and external_id backstop) arbitrate concurrent dupes.
    lead = Lead(
        tenant_id=ctx.tenant_id, external_id=body.external_id, email=str(body.email),
        name=body.name, company=body.company, company_size=body.company_size,
        industry=body.industry, message=body.message, source=body.source,
        status=LeadStatus.RECEIVED,
    )
    db.add(lead)
    db.flush()
    db.add(IdempotencyRecord(tenant_id=ctx.tenant_id, idempotency_key=key, lead_id=lead.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # drops our orphan lead too (same transaction)
        winner = _existing_lead(db, tenant_id=ctx.tenant_id, key=key, external_id=body.external_id)
        if winner is not None:
            return to_lead_out(winner, deduplicated=True)
        raise

    lead_id = lead.id
    # 3. Process now (offline/demo) or hand to Celery (retry-safe background).
    _run_processing(db, lead_id=lead_id, tenant_id=ctx.tenant_id)
    db.refresh(lead)
    return to_lead_out(lead)
