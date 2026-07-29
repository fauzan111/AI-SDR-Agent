"""Pydantic schemas — the structured contract between the webhook, the agents,
and the CRM push."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LeadIn(BaseModel):
    """Inbound lead payload (webhook / form submit)."""

    external_id: str = Field(..., min_length=1, max_length=128, description="ID in source system")
    email: EmailStr
    name: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    company_size: int | None = Field(default=None, ge=0)
    industry: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="webhook", max_length=64)


class LeadOut(BaseModel):
    """A lead's current state."""

    id: str
    external_id: str
    email: str
    company: str | None
    company_size: int | None
    industry: str | None
    status: str
    score: int | None
    notes: str | None
    next_action: str | None
    crm_id: str | None
    meeting_link: str | None
    deduplicated: bool = False  # true when a retry hit an existing lead


class QualificationResult(BaseModel):
    """Output of the qualification agent (hybrid: code + LLM)."""

    score: int = Field(..., ge=0, le=100)
    qualified: bool
    hard_filter_passed: bool
    conversational_fit: int = Field(..., ge=0, le=100)
    notes: str
    decided_by: str  # "code" when hard-filtered out; "llm" when fit was judged
