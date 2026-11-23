"""Platform service: receives the client system's webhooks and runs intake.

Guarantees, because the client's webhooks are at-least-once and our own writes trigger new events:
1. **Authenticity:** HMAC signature is verified before the body is parsed.
2. **Idempotency:** each delivery id is processed once; each incident is triaged once.
3. **No feedback loops:** our work note triggers ``incident.updated``, which intake ignores.
4. **Fast ack:** we answer 202 immediately and process in the background, so the client doesn't retry.

Run:  hao platform serve   (then `hao platform subscribe`)
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from hao.adapters.base import TicketingAdapter
from hao.audit import log as audit
from hao.config import Settings, get_settings
from hao.db.models import HaoAuditEvent, HaoDecision, HaoIntakeResult, HaoProcessedEvent
from hao.db.session import init_db, make_engine, make_session_factory
from hao.intake.classifiers import Classifier, make_classifier
from hao.intake.redaction import Redactor
from hao.mock_ticketing.webhooks import verify
from hao.platform.intake import process_created
from hao.policy.schema import Policy, load_policy


def create_app(settings: Settings, adapter_factory: Callable[[], TicketingAdapter], classifier: Classifier,
               redactor: Redactor | None = None, policy: Policy | None = None) -> FastAPI:
    engine = make_engine(settings.platform_database_url or settings.database_url)
    init_db(engine, prefix="hao_")
    session_factory = make_session_factory(engine)
    redactor = redactor or Redactor()
    policy = policy or load_policy(settings.policy_dir)
    app = FastAPI(title="Human+AI operating model: platform", version="0.3.0")
    app.state.session_factory = session_factory
    app.state.policy = policy

    with session_factory() as session:  # record which policy version is live, once per change
        last = session.scalar(select(HaoAuditEvent).where(HaoAuditEvent.action == "policy.loaded")
                              .order_by(HaoAuditEvent.id.desc()).limit(1))
        if last is None or last.policy_version != policy.version:
            audit.append(session, incident_sys_id="", actor="system:platform", action="policy.loaded",
                         payload={"label": policy.version_label,
                                  "levels": {k: c.current_level.value for k, c in policy.categories.items()}},
                         policy_version=policy.version)

    def run_intake(incident: dict, delivery_id: str) -> None:
        adapter = adapter_factory()
        try:
            process_created(incident, delivery_id=delivery_id, session_factory=session_factory, adapter=adapter,
                            classifier=classifier, redactor=redactor, policy=policy, actor=settings.platform_actor)
        finally:
            if hasattr(adapter, "close"):
                adapter.close()

    @app.get("/health")
    def health():
        return {"status": "ok", "classifier": classifier.name, "policy": policy.version_label,
                "policy_version": policy.version}

    @app.post("/webhooks/ticketing", status_code=202)
    async def receive(
        request: Request,
        tasks: BackgroundTasks,
        x_kaveri_signature: str = Header(""),
        x_kaveri_delivery: str = Header(""),
        x_kaveri_event: str = Header(""),
    ):
        body = await request.body()
        if not verify(body, x_kaveri_signature, settings.webhook_secret):
            raise HTTPException(401, "invalid signature")
        payload = json.loads(body)
        incident = payload.get("incident", {})
        event = payload.get("event", x_kaveri_event)
        if not x_kaveri_delivery or not incident.get("sys_id"):
            raise HTTPException(400, "missing delivery id or incident")

        with session_factory() as session:
            status = "accepted" if event == "incident.created" else "ignored"
            session.add(HaoProcessedEvent(delivery_id=x_kaveri_delivery, event=event,
                                          incident_sys_id=incident["sys_id"], status=status,
                                          detail="" if status == "accepted"
                                          else "intake handles incident.created only"))
            try:
                session.commit()
            except IntegrityError:
                return {"status": "duplicate"}  # at-least-once delivery: already seen this one

        if event == "incident.created":
            tasks.add_task(run_intake, incident, x_kaveri_delivery)
            return {"status": "accepted"}
        return {"status": "ignored"}

    @app.get("/intake/{sys_id}")
    def get_intake(sys_id: str):
        with session_factory() as session:
            r = session.scalar(select(HaoIntakeResult).where(HaoIntakeResult.incident_sys_id == sys_id))
            if r is None:
                raise HTTPException(404, "not triaged")
            return {c.name: getattr(r, c.name) for c in HaoIntakeResult.__table__.columns}

    @app.get("/decisions/{sys_id}")
    def get_decision(sys_id: str):
        """Everything audit asks for: what was decided, by what logic, on what data, by whom, when."""
        with session_factory() as session:
            d = session.scalar(select(HaoDecision).where(HaoDecision.incident_sys_id == sys_id)
                               .order_by(HaoDecision.id.desc()).limit(1))
            if d is None:
                raise HTTPException(404, "no decision")
            return {"decision": {c.name: getattr(d, c.name) for c in HaoDecision.__table__.columns},
                    "audit_trail": audit.trail(session, sys_id)}

    @app.get("/audit/verify")
    def audit_verify(expected_head: str | None = None):
        r = audit.verify(session_factory, expected_head)
        return {"ok": r.ok, "events": r.events, "head_hash": r.head_hash, "broken_at_id": r.broken_at_id,
                "reason": r.reason}

    @app.get("/metrics")
    def metrics():
        with session_factory() as session:
            events = dict(session.execute(select(HaoProcessedEvent.status, func.count())
                                          .group_by(HaoProcessedEvent.status)).all())
            cats = dict(session.execute(select(HaoIntakeResult.category, func.count())
                                        .group_by(HaoIntakeResult.category)).all())
            avg_conf = session.scalar(select(func.avg(HaoIntakeResult.confidence)))
            levels = dict(session.execute(select(HaoDecision.level, func.count()).group_by(HaoDecision.level)).all())
        return {"events": events, "categories": cats, "levels": levels, "avg_confidence": avg_conf}

    return app


def create_app_from_env() -> FastAPI:
    from hao.adapters.servicenow import ServiceNowAdapter

    settings = get_settings()
    return create_app(settings, lambda: ServiceNowAdapter.from_settings(settings),
                      make_classifier(os.environ.get("HAO_CLASSIFIER", "keyword")))
