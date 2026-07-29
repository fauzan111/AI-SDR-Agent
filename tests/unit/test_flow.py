"""End-to-end webhook flow (inline processing) + worker re-validation."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.crm.adapter import CRMAdapter, CRMError
from app.db.base import SessionLocal
from app.db.models import Lead, LeadStatus
from app.main import app
from app.pipeline import process_lead
from app.runtime import get_calendar_singleton, get_crm, get_enricher_singleton


class _FailingCRM(CRMAdapter):
    def upsert_contact(self, contact):  # noqa: ARG002
        raise CRMError("simulated CRM outage")


def _signup(client: TestClient, **icp) -> dict:
    body = {"name": f"co-{uuid.uuid4()}", "icp_min_company_size": 50,
            "icp_industries": "software", "qualification_threshold": 60, **icp}
    r = client.post("/tenants/signup", json=body).json()
    return {"auth": {"Authorization": f"Bearer {r['access_token']}"}, "tenant": r["tenant_id"]}


def test_qualified_lead_pushed_and_scheduled():
    with TestClient(app) as client:
        s = _signup(client)
        lead = client.post("/webhooks/leads", headers=s["auth"], json={
            "external_id": f"L-{uuid.uuid4()}", "email": "vp@bigsoftware.com", "name": "VP Eng",
            "company": "BigSoftware", "company_size": 400, "industry": "software",
            "message": "Budget approved, want a demo and pricing for 60 seats this quarter.",
        }).json()
        assert lead["status"] == "scheduled"
        assert lead["score"] >= 60
        assert lead["crm_id"] and lead["crm_id"].startswith("hs_mock_")
        assert lead["meeting_link"]
        assert lead["next_action"] == "meeting_scheduled"


def test_small_company_is_disqualified():
    with TestClient(app) as client:
        s = _signup(client)
        lead = client.post("/webhooks/leads", headers=s["auth"], json={
            "external_id": f"L-{uuid.uuid4()}", "email": "solo@tinyshop.com",
            "company": "Tiny", "company_size": 3, "industry": "software",
            "message": "just looking",
        }).json()
        assert lead["status"] == "disqualified"
        assert lead["crm_id"] is None
        assert lead["next_action"] == "nurture"


def test_worker_reprocess_is_idempotent_noop():
    """Re-running the pipeline on a terminal lead must not change it or error —
    proves the worker re-validates instead of trusting the queue."""
    with TestClient(app) as client:
        s = _signup(client)
        lead = client.post("/webhooks/leads", headers=s["auth"], json={
            "external_id": f"L-{uuid.uuid4()}", "email": "vp@bigsoftware.com",
            "company": "BigSoftware", "company_size": 400, "industry": "software",
            "message": "Budget approved, want a demo and pricing for 60 seats.",
        }).json()
        assert lead["status"] == "scheduled"

        db = SessionLocal()
        try:
            before = db.get(Lead, lead["id"])
            crm_before, meeting_before = before.crm_id, before.meeting_link
            # Simulate a redelivered Celery message.
            process_lead(db, lead_id=lead["id"], tenant_id=s["tenant"], llm=None,
                         crm=get_crm(), enricher=get_enricher_singleton(),
                         calendar=get_calendar_singleton())
            after = db.get(Lead, lead["id"])
            assert after.status == LeadStatus.SCHEDULED
            assert after.crm_id == crm_before          # unchanged
            assert after.meeting_link == meeting_before
        finally:
            db.close()


def test_failed_crm_push_recovers_on_retry():
    """A CRM outage marks the lead FAILED; a retry (with a working CRM) resumes
    it to SCHEDULED. Proves the pipeline is retry-safe, not one-shot."""
    with TestClient(app) as client:
        s = _signup(client)
        # PROCESS_INLINE with a working mock CRM would schedule immediately, so
        # drive the pipeline directly to inject a failing CRM on the first pass.
        db = SessionLocal()
        try:
            lead = Lead(tenant_id=s["tenant"], external_id=f"L-{uuid.uuid4()}",
                        email="vp@bigsoftware.com", company="BigSoftware", company_size=400,
                        industry="software", message="Budget approved, demo + pricing for 60.",
                        status=LeadStatus.RECEIVED)
            db.add(lead)
            db.commit()
            lead_id = lead.id

            # First pass: CRM is down -> FAILED, and the error propagates.
            try:
                process_lead(db, lead_id=lead_id, tenant_id=s["tenant"], llm=None,
                             crm=_FailingCRM(), enricher=get_enricher_singleton(),
                             calendar=get_calendar_singleton())
                raise AssertionError("expected CRMError")
            except CRMError:
                pass
            assert db.get(Lead, lead_id).status == LeadStatus.FAILED

            # Retry with a working CRM -> resumes to SCHEDULED.
            process_lead(db, lead_id=lead_id, tenant_id=s["tenant"], llm=None,
                         crm=get_crm(), enricher=get_enricher_singleton(),
                         calendar=get_calendar_singleton())
            recovered = db.get(Lead, lead_id)
            assert recovered.status == LeadStatus.SCHEDULED
            assert recovered.crm_id and recovered.meeting_link
        finally:
            db.close()
