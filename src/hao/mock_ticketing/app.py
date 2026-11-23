"""Mock ServiceNow-style ticketing API: the simulated client system.

It is deliberately *not* clean. It reproduces the friction an FDE meets in a real client system
(ADR 0002): string-typed fields, mandatory-field data policies, silently ignored unknown fields,
at-least-once webhooks, plus injectable latency and outages.

Run:  hao mock serve   →  http://localhost:8001/docs
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.orm import Session

from hao.config import REPO_ROOT, Settings, get_settings
from hao.db.models import (
    ClientIncident,
    ClientWebhookDelivery,
    ClientWebhookSubscription,
    ClientWorkNote,
)
from hao.db.session import init_db, make_engine, make_session_factory
from hao.mock_ticketing.serialize import (
    INCIDENT_FIELDS,
    STATES,
    WRITABLE_FIELDS,
    error,
    incident_to_dict,
    parse_sn_datetime,
)
from hao.mock_ticketing.webhooks import deliver_event


class APIError(Exception):
    def __init__(self, status: int, message: str, detail: str = ""):
        self.status, self.message, self.detail = status, message, detail


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def auth(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != request.app.state.settings.mock_api_key:
        raise APIError(401, "User Not Authenticated", "Required to provide Auth information")


DB = Annotated[Session, Depends(get_db)]


def create_app(settings: Settings | None = None, *, webhook_transport: httpx.BaseTransport | None = None,
               webhook_backoff_s: float = 0.5, rng: random.Random | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine, prefix="client_")
    session_factory = make_session_factory(engine)
    chaos_rng = rng or random.Random()

    app = FastAPI(
        title="Kaveri ITSM: mock ticketing API",
        version="0.1.0",
        description="Simulated client ticketing system (ServiceNow-style) for the Human+AI operating model.",
    )
    app.state.session_factory = session_factory
    app.state.settings = settings

    # ------------------------------------------------------------------ plumbing

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content=error(exc.message, exc.detail))

    @app.middleware("http")
    async def chaos(request: Request, call_next):
        """Injected latency and outages, so the platform is built to survive them."""
        if request.url.path.startswith("/api/"):
            if settings.mock_latency_ms:
                await asyncio.sleep(settings.mock_latency_ms / 1000)
            if settings.mock_failure_rate and chaos_rng.random() < settings.mock_failure_rate:
                return JSONResponse(status_code=503, content=error("Service Unavailable",
                                                                   "Instance is under maintenance"))
        return await call_next(request)

    def emit(tasks: BackgroundTasks, event: str, inc: ClientIncident):
        tasks.add_task(
            deliver_event, session_factory, event, incident_to_dict(inc),
            secret=settings.webhook_secret, max_attempts=settings.webhook_max_attempts,
            duplicate_rate=settings.mock_webhook_duplicate_rate, transport=webhook_transport,
            backoff_s=webhook_backoff_s,
        )

    def get_or_404(session: Session, sys_id: str) -> ClientIncident:
        inc = session.get(ClientIncident, sys_id)
        if inc is None:
            raise APIError(404, "No Record found", "Record doesn't exist or ACL restricts the record retrieval")
        return inc

    def next_number(session: Session) -> str:
        current = session.scalar(select(func.max(ClientIncident.number)))
        n = int(current[3:]) + 1 if current else 10001
        return f"INC{n:07d}"

    # ------------------------------------------------------------------ health

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "time": datetime.now(UTC).isoformat()}

    # ------------------------------------------------------------------ incidents

    @app.get("/api/now/table/incident", dependencies=[Depends(auth)], tags=["incident"])
    def list_incidents(
        session: DB,
        sysparm_query: str = Query("", description="e.g. state=1^assignment_group=SD-L1^ORDERBYDESCopened_at"),
        sysparm_limit: int = Query(100, ge=1, le=1000),
        sysparm_offset: int = Query(0, ge=0),
    ):
        stmt = apply_query(select(ClientIncident), sysparm_query)
        rows = session.scalars(stmt.offset(sysparm_offset).limit(sysparm_limit)).all()
        return {"result": [incident_to_dict(r) for r in rows]}

    @app.get("/api/now/table/incident/{sys_id}", dependencies=[Depends(auth)], tags=["incident"])
    def get_incident(sys_id: str, session: DB):
        return {"result": incident_to_dict(get_or_404(session, sys_id))}

    @app.post("/api/now/table/incident", status_code=201, dependencies=[Depends(auth)], tags=["incident"])
    def create_incident(session: DB, tasks: BackgroundTasks, body: Annotated[dict[str, Any], Body()]):
        if not str(body.get("short_description", "")).strip():
            raise APIError(400, "Data Policy Exception", "Short description is mandatory")
        inc = ClientIncident(sys_id=uuid.uuid4().hex, number=next_number(session))
        _apply_fields(inc, body)
        session.add(inc)
        session.commit()
        emit(tasks, "incident.created", inc)
        return {"result": incident_to_dict(inc)}

    @app.patch("/api/now/table/incident/{sys_id}", dependencies=[Depends(auth)], tags=["incident"])
    def update_incident(sys_id: str, session: DB, tasks: BackgroundTasks,
                        body: Annotated[dict[str, Any], Body()], x_actor: Annotated[str, Header()] = "api"):
        inc = get_or_404(session, sys_id)
        if inc.state == "7":
            raise APIError(403, "Operation Failed", "Closed incidents cannot be modified")
        new_state = str(body.get("state", inc.state))
        if new_state not in STATES:
            raise APIError(400, "Invalid state", f"state must be one of {sorted(STATES)}")
        if new_state == "6":
            code = body.get("close_code", inc.close_code)
            notes = body.get("close_notes", inc.close_notes)
            if not str(code).strip() or not str(notes).strip():
                raise APIError(400, "Data Policy Exception",
                               "Close code and Close notes are mandatory when state is Resolved")
        old_state = inc.state
        _apply_fields(inc, body)
        if new_state == "6" and old_state != "6":
            inc.resolved_at = datetime.now(UTC)
        if old_state == "6" and new_state in ("1", "2"):
            inc.reopen_count += 1
            inc.resolved_at = None
        if body.get("work_notes"):
            session.add(ClientWorkNote(incident_sys_id=sys_id, author=x_actor, note=str(body["work_notes"])))
        inc.sys_updated_on = datetime.now(UTC)
        session.commit()
        emit(tasks, "incident.updated", inc)
        return {"result": incident_to_dict(inc)}

    @app.get("/api/now/table/incident/{sys_id}/work_notes", dependencies=[Depends(auth)], tags=["incident"])
    def list_work_notes(sys_id: str, session: DB):
        get_or_404(session, sys_id)
        notes = session.scalars(select(ClientWorkNote).where(ClientWorkNote.incident_sys_id == sys_id)
                                .order_by(ClientWorkNote.id)).all()
        return {"result": [{"kind": n.kind, "author": n.author, "note": n.note,
                            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M:%S")} for n in notes]}

    # ------------------------------------------------------------------ webhooks

    @app.post("/api/x_kaveri/webhooks", status_code=201, dependencies=[Depends(auth)], tags=["webhooks"])
    def subscribe(session: DB, body: Annotated[dict[str, Any], Body()]):
        url = str(body.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise APIError(400, "Invalid URL", "url must be http(s)")
        events = body.get("events", ["incident.created", "incident.updated"])
        sub = ClientWebhookSubscription(url=url, events=",".join(events))
        session.add(sub)
        session.commit()
        return {"result": {"id": str(sub.id), "url": sub.url, "events": events}}

    @app.get("/api/x_kaveri/webhooks", dependencies=[Depends(auth)], tags=["webhooks"])
    def list_subscriptions(session: DB):
        subs = session.scalars(select(ClientWebhookSubscription)).all()
        return {"result": [{"id": str(s.id), "url": s.url, "events": s.events.split(","),
                            "active": "true" if s.active else "false"} for s in subs]}

    @app.delete("/api/x_kaveri/webhooks/{sub_id}", status_code=204, dependencies=[Depends(auth)],
                tags=["webhooks"])
    def unsubscribe(sub_id: int, session: DB):
        sub = session.get(ClientWebhookSubscription, sub_id)
        if sub:
            sub.active = False
            session.commit()

    @app.get("/api/x_kaveri/webhooks/deliveries", dependencies=[Depends(auth)], tags=["webhooks"])
    def list_deliveries(session: DB, limit: int = 100):
        rows = session.scalars(select(ClientWebhookDelivery).order_by(desc(ClientWebhookDelivery.id))
                               .limit(limit)).all()
        return {"result": [{"delivery_id": d.delivery_id, "event": d.event, "incident": d.incident_sys_id,
                            "attempt": str(d.attempt), "status_code": str(d.status_code or ""),
                            "success": "true" if d.success else "false", "error": d.error} for d in rows]}

    # ------------------------------------------------------------------ admin (test harness only)

    @app.post("/admin/seed", dependencies=[Depends(auth)], tags=["admin"])
    def seed(session: DB, tasks: BackgroundTasks, path: str = "data/synthetic/tickets.jsonl",
             limit: int = 0, emit_webhooks: bool = False):
        """Load synthetic tickets as historical backlog. Webhooks are off by default (bulk import)."""
        file = Path(path) if Path(path).is_absolute() else REPO_ROOT / path
        if not file.exists():
            raise APIError(404, "Seed file not found", f"{file}. Run: hao generate")
        loaded = 0
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                if limit and loaded >= limit:
                    break
                row = json.loads(line)
                if session.get(ClientIncident, row["sys_id"]):
                    continue
                inc = ClientIncident(sys_id=row["sys_id"], number=row["number"],
                                     opened_at=parse_sn_datetime(row["opened_at"]))
                _apply_fields(inc, row)
                session.add(inc)
                loaded += 1
                if emit_webhooks:
                    session.flush()
                    emit(tasks, "incident.created", inc)
        session.commit()
        return {"result": {"loaded": str(loaded)}}

    @app.post("/admin/reset", dependencies=[Depends(auth)], tags=["admin"])
    def reset(session: DB):
        for model in (ClientWorkNote, ClientWebhookDelivery, ClientWebhookSubscription, ClientIncident):
            session.execute(delete(model))
        session.commit()
        return {"result": {"reset": "true"}}

    return app


def _apply_fields(inc: ClientIncident, body: dict[str, Any]) -> None:
    """Unknown fields are silently ignored, exactly like ServiceNow. A classic integration trap."""
    for key, value in body.items():
        if key not in WRITABLE_FIELDS:
            continue
        if key == "u_vip":
            inc.u_vip = str(value).lower() == "true"
        else:
            setattr(inc, key, "" if value is None else str(value))


def apply_query(stmt, query: str):
    """Tiny subset of ServiceNow encoded queries: field=v, field!=v, fieldINa,b, ORDERBY[DESC]field."""
    for part in filter(None, (p.strip() for p in query.split("^"))):
        if part.startswith("ORDERBYDESC"):
            stmt = stmt.order_by(desc(_col(part[11:])))
        elif part.startswith("ORDERBY"):
            stmt = stmt.order_by(asc(_col(part[7:])))
        elif "!=" in part:
            field, value = part.split("!=", 1)
            stmt = stmt.where(_col(field) != _coerce(field, value))
        elif "=" in part:
            field, value = part.split("=", 1)
            stmt = stmt.where(_col(field) == _coerce(field, value))
        elif "IN" in part:
            field, values = part.split("IN", 1)
            stmt = stmt.where(_col(field).in_([_coerce(field, v) for v in values.split(",")]))
        else:
            raise APIError(400, "Invalid query", f"Unsupported query term: {part}")
    return stmt


def _col(field: str):
    if field not in INCIDENT_FIELDS:
        raise APIError(400, "Invalid query", f"Unknown field: {field}")
    return getattr(ClientIncident, field)


def _coerce(field: str, value: str):
    if field == "u_vip":
        return value.lower() == "true"
    if field == "reopen_count":
        return int(value)
    return value
