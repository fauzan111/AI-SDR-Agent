"""Multi-tenant data model for the SDR agent.

Every business row carries a ``tenant_id``. The idempotency spine is
:class:`IdempotencyRecord` + a unique constraint on
``(tenant_id, idempotency_key)``: a retried inbound webhook can never create a
duplicate :class:`Lead`, even under concurrency. The :class:`AuditLog` table is
the EU AI Act / GDPR trail (unchanged from the foundation).
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- ICP (ideal customer profile) — the deterministic hard filters ---
    icp_min_company_size: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    # Comma-separated allowed industries; empty = any industry passes.
    icp_industries: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    qualification_threshold: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LeadStatus(str, enum.Enum):
    RECEIVED = "received"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    PUSHED_TO_CRM = "pushed_to_crm"
    SCHEDULED = "scheduled"
    FAILED = "failed"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)

    # The lead's identity in the source system; part of the dedupe key.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="webhook", nullable=False)

    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus), default=LeadStatus.RECEIVED, nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        Index("ix_leads_tenant_status", "tenant_id", "status"),
        # Backstop: even if the idempotency key source is inconsistent across
        # retries (header vs external_id), a lead's identity in the source system
        # can only ever produce ONE row per tenant.
        UniqueConstraint("tenant_id", "external_id", name="uq_leads_tenant_external"),
    )


class IdempotencyRecord(Base):
    """Maps a tenant-scoped idempotency key to the lead it created.

    The UNIQUE constraint is the real dedupe guarantee: two concurrent inserts
    with the same key cannot both succeed — the loser catches IntegrityError and
    returns the winner's lead. This is enforced by the database, not the app.
    """

    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_tenant_key"),
    )


class AuditLog(Base):
    """Immutable-by-convention record of a single agent decision (EU AI Act)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)  # "llm" | "code"
    output: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)
