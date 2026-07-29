"""CRM adapter pattern, built against HubSpot's real API shape.

The rest of the app depends only on :class:`CRMAdapter.upsert_contact`. Two
implementations:

- ``MockHubSpotAdapter`` — offline, deterministic. Upsert is keyed on email so a
  retry maps to the *same* CRM id (idempotent by construction), which is exactly
  how HubSpot's email-keyed upsert behaves.
- ``HubSpotAdapter`` — calls the real HubSpot v3 contacts upsert endpoint with a
  bearer token. Enabled with CRM_PROVIDER=hubspot + HUBSPOT_ACCESS_TOKEN, so the
  integration is honest ("integrates with HubSpot"), not just simulated.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CRMContact:
    email: str
    name: str | None
    company: str | None
    score: int
    notes: str


class CRMError(RuntimeError):
    """Raised when a CRM push fails; the pipeline marks the lead FAILED so a
    retry (Celery or a re-sent webhook) can safely try again."""


class CRMAdapter(ABC):
    @abstractmethod
    def upsert_contact(self, contact: CRMContact) -> str:
        """Create or update the contact; return the stable CRM id."""


class MockHubSpotAdapter(CRMAdapter):
    def upsert_contact(self, contact: CRMContact) -> str:
        # Deterministic id from email == HubSpot's email-keyed upsert semantics.
        digest = hashlib.md5(contact.email.lower().encode()).hexdigest()[:12]
        return f"hs_mock_{digest}"


class HubSpotAdapter(CRMAdapter):
    """Real HubSpot CRM v3. Uses the email-keyed upsert so repeated pushes for
    the same lead converge on one contact (idempotent server-side too)."""

    BASE = "https://api.hubapi.com"

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    def upsert_contact(self, contact: CRMContact) -> str:
        first, _, last = (contact.name or "").partition(" ")
        properties = {
            "email": contact.email,
            "firstname": first or None,
            "lastname": last or None,
            "company": contact.company,
            "hs_lead_status": "QUALIFIED",
            "sdr_agent_score": contact.score,
        }
        # HubSpot batch upsert keyed on the email property.
        body = {
            "inputs": [
                {"idProperty": "email", "id": contact.email,
                 "properties": {k: v for k, v in properties.items() if v is not None}}
            ]
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.BASE}/crm/v3/objects/contacts/batch/upsert",
            data=data,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed HubSpot host
                payload = json.loads(resp.read())
            return str(payload["results"][0]["id"])
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as exc:
            raise CRMError(f"HubSpot upsert failed: {exc}") from exc


def get_crm_adapter(provider: str, hubspot_token: str = "") -> CRMAdapter:
    if provider == "hubspot" and hubspot_token:
        return HubSpotAdapter(hubspot_token)
    return MockHubSpotAdapter()
