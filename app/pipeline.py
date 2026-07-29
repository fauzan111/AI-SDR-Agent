"""Lead-processing pipeline: enrich -> qualify -> (CRM push + schedule).

Called two ways with identical behavior: inline from the webhook, or from the
Celery task. It is **idempotent and re-validating**: it re-reads the lead and
only acts when the lead is in a non-terminal state. A retried task/webhook that
lands on an already-processed lead is a safe no-op — the worker never trusts the
queue, it re-checks the database.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.qualification import build_qualification_graph, score_from_state
from app.crm.adapter import CRMAdapter, CRMContact, CRMError
from app.db.models import AuditLog, Lead, LeadStatus, Tenant
from app.enrichment.enricher import Enricher
from app.llm.client import LLMClient
from app.observability.logging import get_logger
from app.scheduling.calendar import CalendarAdapter

log = get_logger("pipeline")

# States that mean "already handled" — reprocessing them would be wrong.
_TERMINAL = {LeadStatus.DISQUALIFIED, LeadStatus.SCHEDULED}


def process_lead(
    db: Session,
    *,
    lead_id: str,
    tenant_id: str,
    llm: LLMClient | None,
    crm: CRMAdapter,
    enricher: Enricher,
    calendar: CalendarAdapter,
) -> None:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != tenant_id:
        log.warning("pipeline.lead_missing", lead_id=lead_id, tenant_id=tenant_id)
        return
    # Re-validate BEFORE acting: idempotent against retried webhooks / tasks.
    if lead.status in _TERMINAL:
        log.info("pipeline.skip_terminal", lead_id=lead_id, status=lead.status.value)
        return

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        return

    # Resume path: a lead already pushed to the CRM must NOT be re-qualified
    # (a non-deterministic LLM could regress an already-pushed lead). Skip
    # straight to the remaining scheduling step.
    if lead.status != LeadStatus.PUSHED_TO_CRM:
        _enrich(lead, enricher)
        result = _qualify(lead, tenant, llm)
        db.add(AuditLog(tenant_id=tenant_id, lead_id=lead.id, actor="qualification-agent",
                        action="lead.qualify", decided_by=result.decided_by, output=result.notes))
        lead.score = result.score
        lead.notes = result.notes

        if not result.qualified:
            lead.status = LeadStatus.DISQUALIFIED
            lead.next_action = "nurture"
            db.commit()
            return

        # Push to CRM. Failures are recoverable — mark FAILED so a retry re-runs
        # (email-keyed upsert converges on the same contact, so no duplicate).
        try:
            lead.crm_id = crm.upsert_contact(CRMContact(
                email=lead.email, name=lead.name, company=lead.company,
                score=result.score, notes=result.notes,
            ))
        except CRMError as exc:
            lead.status = LeadStatus.FAILED
            lead.next_action = "retry_crm_push"
            db.add(AuditLog(tenant_id=tenant_id, lead_id=lead.id, actor="crm-adapter",
                            action="crm.push_failed", decided_by="code", output=str(exc)))
            db.commit()
            raise

        lead.status = LeadStatus.PUSHED_TO_CRM
        db.add(AuditLog(tenant_id=tenant_id, lead_id=lead.id, actor="crm-adapter",
                        action="crm.pushed", decided_by="code", output=f"crm_id={lead.crm_id}"))
        db.commit()  # persist PUSHED_TO_CRM so a crash before scheduling can resume here

    # Scheduling handoff (reached fresh, or on resume of a PUSHED_TO_CRM lead).
    lead.meeting_link = calendar.create_booking(email=lead.email, lead_id=lead.id)
    lead.status = LeadStatus.SCHEDULED
    lead.next_action = "meeting_scheduled"
    db.add(AuditLog(tenant_id=tenant_id, lead_id=lead.id, actor="calendar-adapter",
                    action="meeting.scheduled", decided_by="code", output=lead.meeting_link))
    db.commit()


def _enrich(lead: Lead, enricher: Enricher) -> None:
    e = enricher.enrich(email=lead.email, company=lead.company)
    if lead.company is None and e.company:
        lead.company = e.company
    if lead.company_size is None and e.company_size is not None:
        lead.company_size = e.company_size
    if lead.industry is None and e.industry:
        lead.industry = e.industry


def _qualify(lead: Lead, tenant: Tenant, llm: LLMClient | None):
    graph = build_qualification_graph(llm)
    icp_industries = [i for i in tenant.icp_industries.split(",") if i.strip()]
    final = graph.invoke({
        "company_size": lead.company_size,
        "industry": lead.industry,
        "message": lead.message,
        "icp_min_company_size": tenant.icp_min_company_size,
        "icp_industries": icp_industries,
        "threshold": tenant.qualification_threshold,
    })
    return score_from_state(final, tenant.qualification_threshold)
