"""Meeting-scheduling handoff for qualified leads only.

The default ``MockCalendar`` returns a deterministic booking link so the flow is
demoable offline. Swap in Google Calendar / Calendly behind the same seam.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod


class CalendarAdapter(ABC):
    @abstractmethod
    def create_booking(self, *, email: str, lead_id: str) -> str:
        """Return a booking link/URL for the qualified lead."""


class MockCalendar(CalendarAdapter):
    def create_booking(self, *, email: str, lead_id: str) -> str:
        token = hashlib.md5(f"{lead_id}:{email}".encode()).hexdigest()[:10]
        return f"https://cal.example.com/sdr-intro/{token}"


def get_calendar() -> CalendarAdapter:
    return MockCalendar()
