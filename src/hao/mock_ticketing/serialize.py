"""ServiceNow-style serialisation quirks, reproduced on purpose.

* Every value is a string (booleans are "true"/"false", numbers are "3", null is "").
* Datetimes are "YYYY-MM-DD HH:MM:SS" in UTC, with no timezone marker.
* Responses are wrapped in {"result": ...}; errors in {"error": {...}, "status": "failure"}.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hao.db.models import ClientIncident

INCIDENT_FIELDS = (
    "sys_id", "number", "opened_at", "sys_updated_on", "caller_id", "caller_email", "u_vip", "location",
    "contact_type", "short_description", "description", "category", "cmdb_ci", "priority", "state",
    "assignment_group", "assigned_to", "close_code", "close_notes", "resolved_at", "reopen_count",
)
WRITABLE_FIELDS = frozenset(INCIDENT_FIELDS) - {"sys_id", "number", "opened_at", "sys_updated_on",
                                                "resolved_at", "reopen_count"}

STATES = {"1": "New", "2": "In Progress", "3": "On Hold", "6": "Resolved", "7": "Closed", "8": "Canceled"}


def sn_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def parse_sn_datetime(value: str) -> datetime:
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_sn(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return sn_datetime(value)
    return str(value)


def incident_to_dict(inc: ClientIncident) -> dict[str, str]:
    return {f: to_sn(getattr(inc, f)) for f in INCIDENT_FIELDS}


def error(message: str, detail: str = "") -> dict:
    return {"error": {"message": message, "detail": detail}, "status": "failure"}
