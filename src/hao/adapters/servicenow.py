"""ServiceNow-style adapter (works against the mock and, with auth changes, a real instance).

Encodes the integration lessons from Phase 1's mock:
* retries with exponential backoff + jitter on 429/5xx and connection errors
* string fields → typed ``Incident``
* **read-after-write check**, because unknown fields are silently ignored rather than rejected
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime

import httpx

from hao.adapters.base import FieldNotApplied, Incident, TicketingError, TicketingUnavailable

RETRY_STATUSES = {429, 500, 502, 503, 504}


def _dt(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_incident(raw: dict[str, str]) -> Incident:
    return Incident(
        sys_id=raw["sys_id"],
        number=raw.get("number", ""),
        opened_at=_dt(raw.get("opened_at", "")),
        updated_at=_dt(raw.get("sys_updated_on", "") or raw.get("opened_at", "")),
        caller_name=raw.get("caller_id", ""),
        caller_email=raw.get("caller_email", ""),
        vip=raw.get("u_vip", "false") == "true",
        location=raw.get("location", ""),
        contact_type=raw.get("contact_type", ""),
        short_description=raw.get("short_description", ""),
        description=raw.get("description", ""),
        user_category=raw.get("category", ""),
        configuration_item=raw.get("cmdb_ci", ""),
        priority=int(raw.get("priority") or 4),
        state=raw.get("state", ""),
        assignment_group=raw.get("assignment_group", ""),
        reopen_count=int(raw.get("reopen_count") or 0),
        raw=raw,
    )


class ServiceNowAdapter:
    def __init__(self, client: httpx.Client, api_key: str, *, actor: str = "ai:hao", max_attempts: int = 4,
                 backoff_s: float = 0.5, rng: random.Random | None = None, owns_client: bool = False):
        self._client = client
        self._owns_client = owns_client
        self._headers = {"X-API-Key": api_key, "X-Actor": actor, "Accept": "application/json"}
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._rng = rng or random.Random()
        self.retries = 0  # exposed for observability and tests

    @classmethod
    def from_settings(cls, settings) -> ServiceNowAdapter:
        return cls(httpx.Client(base_url=settings.ticketing_base_url, timeout=10.0), settings.mock_api_key,
                   actor=settings.platform_actor, owns_client=True)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = self._client.request(method, path, headers=self._headers, **kwargs)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code not in RETRY_STATUSES:
                    if resp.status_code >= 400:
                        err = resp.json().get("error", {}) if resp.content else {}
                        raise TicketingError(f"{resp.status_code} {err.get('message', '')}: {err.get('detail', '')}")
                    return resp
                last_error = f"HTTP {resp.status_code}"
            if attempt < self._max_attempts:
                self.retries += 1
                delay = self._backoff_s * 2 ** (attempt - 1)
                time.sleep(delay + self._rng.uniform(0, delay / 2))
        raise TicketingUnavailable(f"{method} {path} failed after {self._max_attempts} attempts ({last_error})")

    # ------------------------------------------------------------------ API

    def get_incident(self, sys_id: str) -> Incident:
        return to_incident(self._request("GET", f"/api/now/table/incident/{sys_id}").json()["result"])

    def list_incidents(self, query: str = "", limit: int = 100) -> list[Incident]:
        resp = self._request("GET", "/api/now/table/incident", params={"sysparm_query": query, "sysparm_limit": limit})
        return [to_incident(r) for r in resp.json()["result"]]

    def update_incident(self, sys_id: str, fields: dict[str, str]) -> Incident:
        result = self._request("PATCH", f"/api/now/table/incident/{sys_id}", json=fields).json()["result"]
        journal = {"work_notes", "comments"}
        not_applied = {k: v for k, v in fields.items() if k not in journal and result.get(k) != str(v)}
        if not_applied:
            raise FieldNotApplied(f"client system ignored fields on {sys_id}: {sorted(not_applied)}")
        return to_incident(result)

    def add_work_note(self, sys_id: str, note: str) -> None:
        self._request("PATCH", f"/api/now/table/incident/{sys_id}", json={"work_notes": note})

    def add_comment(self, sys_id: str, comment: str) -> None:
        """Customer-visible reply (work notes are internal)."""
        self._request("PATCH", f"/api/now/table/incident/{sys_id}", json={"comments": comment})

    def resolve(self, sys_id: str, close_code: str, close_notes: str) -> Incident:
        return self.update_incident(sys_id, {"state": "6", "close_code": close_code, "close_notes": close_notes})

    def assign(self, sys_id: str, group: str) -> Incident:
        return self.update_incident(sys_id, {"assignment_group": group})

    def list_kb(self) -> list[dict]:
        return self._request("GET", "/api/now/table/kb_knowledge").json()["result"]

    def identity(self, action: str, user_email: str, application: str, sys_id: str) -> dict:
        body = {"user_email": user_email, "application": application, "incident_sys_id": sys_id}
        return self._request("POST", f"/api/x_kaveri/identity/{action}", json=body).json()["result"]

    def request_software(self, package: str, user_email: str, sys_id: str) -> dict:
        body = {"package": package, "user_email": user_email, "incident_sys_id": sys_id}
        return self._request("POST", "/api/x_kaveri/software/requests", json=body).json()["result"]

    def with_actor(self, actor: str) -> ServiceNowAdapter:
        """Same connection, different actor: a human approver's actions are attributed to them."""
        clone = ServiceNowAdapter(self._client, self._headers["X-API-Key"], actor=actor,
                                  max_attempts=self._max_attempts, backoff_s=self._backoff_s, rng=self._rng)
        return clone

    def subscribe_webhook(self, url: str) -> dict:
        return self._request("POST", "/api/x_kaveri/webhooks", json={"url": url}).json()["result"]
