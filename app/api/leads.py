"""Read API for leads (tenant-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.webhooks import to_lead_out
from app.auth.tenant import TenantContext, get_tenant_context
from app.db.base import get_db
from app.db.models import Lead
from app.schemas import LeadOut

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=list[LeadOut])
def list_leads(
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[LeadOut]:
    rows = db.scalars(
        select(Lead).where(Lead.tenant_id == ctx.tenant_id).order_by(Lead.created_at.desc())
    ).all()
    return [to_lead_out(lead) for lead in rows]


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(
    lead_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> LeadOut:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != ctx.tenant_id:  # tenant scoping
        raise HTTPException(status_code=404, detail="Lead not found")
    return to_lead_out(lead)
