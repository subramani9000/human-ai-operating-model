"""The only way platform code talks to a client ticketing system (ADR 0002).

Swapping ServiceNow for Jira Service Management means writing one class that satisfies this protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class Incident(BaseModel):
    """Typed, normalised view of a client ticket. Adapters convert the client's quirks into this."""

    sys_id: str
    number: str
    opened_at: datetime
    updated_at: datetime
    caller_name: str
    caller_email: str
    vip: bool
    location: str
    contact_type: str
    short_description: str
    description: str
    user_category: str
    configuration_item: str
    priority: int
    state: str
    assignment_group: str
    reopen_count: int
    raw: dict[str, str]  # original payload, kept for audit and debugging


class TicketingError(Exception):
    """Base class for integration failures."""


class TicketingUnavailable(TicketingError):
    """Client system down or rate-limiting after all retries."""


class FieldNotApplied(TicketingError):
    """The client system accepted the update but silently ignored a field."""


class TicketingAdapter(Protocol):
    def get_incident(self, sys_id: str) -> Incident: ...

    def list_incidents(self, query: str = "", limit: int = 100) -> list[Incident]: ...

    def update_incident(self, sys_id: str, fields: dict[str, str]) -> Incident: ...

    def add_work_note(self, sys_id: str, note: str) -> None: ...

    def add_comment(self, sys_id: str, comment: str) -> None: ...

    def resolve(self, sys_id: str, close_code: str, close_notes: str) -> Incident: ...

    def assign(self, sys_id: str, group: str) -> Incident: ...

    def list_kb(self) -> list[dict]: ...

    def identity(self, action: str, user_email: str, application: str, sys_id: str) -> dict: ...

    def request_software(self, package: str, user_email: str, sys_id: str) -> dict: ...

    def with_actor(self, actor: str) -> TicketingAdapter: ...
