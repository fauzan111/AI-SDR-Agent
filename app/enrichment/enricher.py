"""Lead enrichment: fill missing company/contact fields from public signals.

The default ``DomainEnricher`` derives what it can from the email domain with no
network call (offline-friendly, deterministic). Swap in a real provider
(Clearbit, Apollo, etc.) behind the same ``Enricher.enrich`` seam.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# Free/consumer email domains: not a company, so company-derived enrichment
# should not invent a size/industry for them.
_CONSUMER_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}

# Toy heuristic mapping so the demo shows enrichment doing *something* concrete.
_INDUSTRY_HINTS = {
    "shop": "retail", "store": "retail", "commerce": "retail",
    "bank": "finance", "capital": "finance", "pay": "finance",
    "health": "healthcare", "care": "healthcare", "med": "healthcare",
    "tech": "software", "ai": "software", "cloud": "software", "data": "software",
}


@dataclass
class Enrichment:
    company: str | None = None
    company_size: int | None = None
    industry: str | None = None
    is_business_email: bool = True


class Enricher(ABC):
    @abstractmethod
    def enrich(self, *, email: str, company: str | None) -> Enrichment: ...


class DomainEnricher(Enricher):
    def enrich(self, *, email: str, company: str | None) -> Enrichment:
        domain = email.split("@")[-1].lower() if "@" in email else ""
        if domain in _CONSUMER_DOMAINS or not domain:
            return Enrichment(is_business_email=False)

        root = domain.split(".")[0]
        inferred_company = company or root.capitalize()
        industry = next((ind for hint, ind in _INDUSTRY_HINTS.items() if hint in root), None)
        # Deterministic pseudo-size from the domain so the demo has a number.
        size = 10 + (sum(ord(c) for c in root) % 490)
        return Enrichment(
            company=inferred_company,
            company_size=size,
            industry=industry,
            is_business_email=True,
        )


def get_enricher() -> Enricher:
    return DomainEnricher()
