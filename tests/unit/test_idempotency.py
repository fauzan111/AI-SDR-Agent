"""The headline guarantee: a retried webhook never creates a duplicate lead.

Covers the fast path (same key seen again) and confirms only one lead + one
idempotency record exist afterward. Concurrency is exercised separately in
scripts/retry_test.py against a live server.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.db.base import SessionLocal
from app.db.models import IdempotencyRecord, Lead
from app.main import app


def _signup(client: TestClient) -> dict:
    r = client.post("/tenants/signup", json={"name": f"acme-{uuid.uuid4()}",
                                             "icp_min_company_size": 0})
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}", "_tenant": body["tenant_id"]}


def _payload(ext_id: str) -> dict:
    return {"external_id": ext_id, "email": "buyer@acme-corp.com", "name": "Pat Buyer",
            "company": "Acme Corp", "company_size": 200, "industry": "software",
            "message": "We need pricing for a team of 40, evaluating this quarter."}


def test_retried_webhook_is_deduplicated():
    with TestClient(app) as client:
        headers = _signup(client)
        auth = {"Authorization": headers["Authorization"]}
        ext_id = f"lead-{uuid.uuid4()}"

        first = client.post("/webhooks/leads", headers=auth, json=_payload(ext_id)).json()
        second = client.post("/webhooks/leads", headers=auth, json=_payload(ext_id)).json()

        assert first["id"] == second["id"]          # same lead
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True        # retry recognized

        # Exactly one lead + one idempotency record for this tenant.
        db = SessionLocal()
        try:
            tenant_id = headers["_tenant"]
            leads = db.query(Lead).filter(Lead.tenant_id == tenant_id).all()
            recs = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.tenant_id == tenant_id).all()
            assert len(leads) == 1
            assert len(recs) == 1
        finally:
            db.close()


def test_explicit_idempotency_key_header_dedupes_across_external_ids():
    with TestClient(app) as client:
        auth = {"Authorization": _signup(client)["Authorization"], "Idempotency-Key": "fixed-123"}
        a = client.post("/webhooks/leads", headers=auth, json=_payload("ext-A")).json()
        b = client.post("/webhooks/leads", headers=auth, json=_payload("ext-B")).json()
        # Same idempotency key -> same lead, even though external_id differs.
        assert a["id"] == b["id"]
        assert b["deduplicated"] is True


def test_inconsistent_key_source_still_dedupes_by_external_id():
    """The M1 backstop: same lead (external_id), but one request carries an
    Idempotency-Key header and the other doesn't -> still ONE lead."""
    with TestClient(app) as client:
        headers = _signup(client)
        auth = {"Authorization": headers["Authorization"]}
        ext_id = f"lead-{uuid.uuid4()}"

        without_header = client.post("/webhooks/leads", headers=auth, json=_payload(ext_id)).json()
        with_header = client.post(
            "/webhooks/leads", headers={**auth, "Idempotency-Key": "some-proxy-key"},
            json=_payload(ext_id),
        ).json()

        assert without_header["id"] == with_header["id"]  # no duplicate
        assert with_header["deduplicated"] is True

        db = SessionLocal()
        try:
            leads = db.query(Lead).filter(Lead.tenant_id == headers["_tenant"]).all()
            assert len(leads) == 1
        finally:
            db.close()
